from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login, logout, authenticate
from django.contrib import messages
from django.http import (
    FileResponse, Http404, HttpResponse, HttpResponseForbidden,
    HttpResponseRedirect, JsonResponse,
)
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html_join
from django.utils.http import content_disposition_header, url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django.conf import settings
from django.db import transaction
from django.db.models import Count, Sum, Q
from decimal import Decimal
import os

from .models import (
    WorkOrder, WorkOrderLanguage, ServiceRecord,
    WorkOrderApproval, CompletionCertificate,
    Prosecution, Prosecutor, Language, UserProfile,
    TranslatorProfile, OrderAssignment, WorkflowConfig,
    ConferenceRecording, OrderDocument, DocumentNote, content_type_for,
)
from .forms import (
    WorkOrderForm, WorkOrderLanguageFormSet,
    ServiceRecordForm, MeetingLinkForm, DisputeForm,
    AssignTranslatorForm, TranslatorServiceForm,
    DocumentUploadForm, DocumentNoteForm,
)
from .signals import (
    notify_new_order, notify_order_accepted, notify_meeting_link,
    notify_actuals_logged, notify_pp_approved, notify_pp_disputed,
    notify_translator_assigned, notify_assignment_accepted,
    notify_assignment_declined, notify_all_assignments_completed,
    notify_source_documents_uploaded, notify_translation_uploaded,
    notify_revision_requested,
)
from .livekit_utils import (
    create_room, generate_join_token, update_room_metadata,
    start_room_recording, stop_recording, list_egress,
)
from glossary.captions import glossary_prompt


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return redirect(_safe_redirect_url(request, request.GET.get('next', ''), '/'))
        else:
            messages.error(request, 'اسم المستخدم أو كلمة المرور غير صحيحة')
    return render(request, 'registration/login.html')


@login_required
def logout_view(request):
    logout(request)
    return redirect('login')


def _get_profile(user):
    try:
        return user.profile
    except UserProfile.DoesNotExist:
        return None


def _visible_orders(user, profile):
    """Work orders the user may read — the single source of read access."""
    orders = WorkOrder.objects.all()
    if user.is_superuser:
        return orders
    if profile is None:
        return orders.none()
    if profile.is_smartworld or profile.is_contract_manager:
        return orders
    if profile.is_pp_staff:
        return orders.filter(
            Q(created_by=user) | Q(prosecution=profile.prosecution)
        )
    if profile.is_translator:
        visible = Q(assignments__translator=user)
        # AUTO mode lists open lines in the translator's languages and links
        # to their orders, so those must be readable too.
        if WorkflowConfig.get_config().mode == WorkflowConfig.Mode.AUTO:
            visible |= Q(
                status__in=[WorkOrder.Status.ACCEPTED, WorkOrder.Status.ASSIGNED],
                languages__language__translatorprofile__user=user,
            )
        return orders.filter(visible).distinct()
    return orders.none()


def _can_view_order(user, profile, order):
    return _visible_orders(user, profile).filter(pk=order.pk).exists()


def _can_finalize_certificate(user, profile):
    return user.is_superuser or bool(profile and profile.is_smartworld_admin)


def _safe_redirect_url(request, url, fallback):
    """``url`` if it stays on this site, otherwise ``fallback``."""
    if url_has_allowed_host_and_scheme(
        url, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
    ):
        return url
    return fallback


# ── Document permissions & helpers ────────────────────────────────────

def _can_access_documents(user, profile, order):
    """Readers of the order, except translators only see files of orders they work on."""
    if not _can_view_order(user, profile, order):
        return False
    if user.is_superuser or not (profile and profile.is_translator):
        return True
    return order.assignments.filter(translator=user).exclude(
        status=OrderAssignment.Status.DECLINED
    ).exists()


def _is_document_staff(user, profile):
    return user.is_superuser or bool(
        profile and (profile.is_smartworld or profile.is_contract_manager)
    )


def _can_supply_and_review_documents(user, profile, order):
    """PP staff of the order, SmartWorld and the CM upload files to translate
    and raise revision notes on translations."""
    if order.status == WorkOrder.Status.COMPLETED:
        return False
    if _is_document_staff(user, profile):
        return True
    return bool(profile and profile.is_pp_staff) and _can_view_order(user, profile, order)


def _can_upload_translation(user, profile, order, language_line):
    if order.status == WorkOrder.Status.COMPLETED:
        return False
    if _is_document_staff(user, profile):
        return True
    return bool(profile and profile.is_translator) and language_line.assignments.filter(
        translator=user,
        status__in=[OrderAssignment.Status.ACCEPTED, OrderAssignment.Status.COMPLETED],
    ).exists()


def _can_delete_document(user, order, document):
    return order.status != WorkOrder.Status.COMPLETED and (
        user.is_superuser or document.uploaded_by_id == user.pk
    )


def _accept_attribute():
    return ','.join(f'.{ext}' for ext in settings.DOCUMENT_ALLOWED_EXTENSIONS)


def _document_context(request, order, profile):
    """Context for the documents card: source files, translations per line, notes."""
    user = request.user
    documents = list(order.documents.select_related('uploaded_by'))
    for document in documents:
        document.can_delete = _can_delete_document(user, order, document)
    notes = list(order.document_notes.select_related(
        'author', 'addressed_by', 'document', 'addressed_by_document',
    ))

    lines = []
    for line in order.languages.select_related('language'):
        translations = [
            d for d in documents
            if d.kind == OrderDocument.Kind.TRANSLATION and d.language_line_id == line.pk
        ]
        line_notes = [n for n in notes if n.language_line_id == line.pk]
        lines.append({
            'line': line,
            'translations': translations,
            'latest_pk': translations[0].pk if translations else None,
            'notes': line_notes,
            'open_notes': sum(n.status == DocumentNote.Status.OPEN for n in line_notes),
            'can_upload': _can_upload_translation(user, profile, order, line),
        })

    used = sum(d.size for d in documents if d.purged_at is None)
    limit = settings.DOCUMENT_MAX_ORDER_TOTAL_SIZE
    can_supply = _can_supply_and_review_documents(user, profile, order)
    return {
        'can_access_documents': _can_access_documents(user, profile, order),
        'can_upload_source': can_supply,
        'can_add_note': can_supply,
        'source_documents': [d for d in documents if d.kind == OrderDocument.Kind.SOURCE],
        'translation_lines': lines,
        'open_notes_count': sum(item['open_notes'] for item in lines),
        'lines_missing_translation': sum(1 for item in lines if not item['translations']),
        'documents_used': used,
        'documents_limit': limit,
        'documents_used_percent': min(100, used * 100 // limit) if limit else 0,
        'allowed_extensions': _accept_attribute(),
    }


def _glossary_order_context(request, order):
    """Context for the "مصطلحات هذا الأمر" card and the glossary lookup panel."""
    from glossary.permissions import can_link_terms  # glossary.permissions imports this module
    return {
        'order_terms': order.glossary_terms.filter(term__is_archived=False).select_related(
            'term', 'term__domain', 'added_by',
        ),
        'can_link_terms': can_link_terms(request.user, order),
    }


def _translator_assignments(user, limit=None):
    """A translator's assignments by status, with source-file and open-note counts."""
    assignments = OrderAssignment.objects.filter(translator=user).select_related(
        'work_order', 'work_order__prosecution', 'language_line', 'language_line__language',
    ).annotate(
        source_count=Count(
            'work_order__documents', distinct=True,
            filter=Q(work_order__documents__kind=OrderDocument.Kind.SOURCE),
        ),
        open_notes=Count(
            'language_line__document_notes', distinct=True,
            filter=Q(language_line__document_notes__status=DocumentNote.Status.OPEN),
        ),
    )

    def by_status(status, default_limit=None):
        rows = assignments.filter(status=status)
        cap = limit or default_limit
        return rows[:cap] if cap else rows

    return {
        'pending_assignments': by_status(OrderAssignment.Status.PENDING),
        'active_assignments': by_status(OrderAssignment.Status.ACCEPTED),
        'completed_assignments': by_status(OrderAssignment.Status.COMPLETED, 20),
        'revision_assignments': assignments.filter(
            status=OrderAssignment.Status.COMPLETED, open_notes__gt=0,
        ),
    }


def _save_documents(order, files, kind, user, *, language_line=None, assignment=None, note=''):
    """Store uploaded files as OrderDocument rows. Call inside a transaction."""
    scan_status = (
        OrderDocument.ScanStatus.CLEAN if settings.DOCUMENT_VIRUS_SCAN == 'required'
        else OrderDocument.ScanStatus.NOT_SCANNED
    )
    documents = []
    try:
        for uploaded in files:
            document = OrderDocument(
                work_order=order, kind=kind, language_line=language_line,
                assignment=assignment, uploaded_by=user, note=note,
                original_name=os.path.basename(uploaded.name)[:255],
                size=uploaded.size, content_type=content_type_for(uploaded.name),
                scan_status=scan_status,
            )
            document.file.save(uploaded.name, uploaded, save=False)
            documents.append(document)
            document.save()
    except Exception:
        # Don't leave stored files behind when their rows weren't created.
        for document in documents:
            document.file.storage.delete(document.file.name)
        raise
    return documents


def _save_translation(order, language_line, files, user, *, assignment=None, note=''):
    """Store a translation; it addresses the line's open revision notes."""
    documents = _save_documents(
        order, files, OrderDocument.Kind.TRANSLATION, user,
        language_line=language_line, assignment=assignment, note=note,
    )
    addressed = DocumentNote.address_open_notes(language_line, documents[0], user)
    return documents, addressed


def _documents_url(order, anchor='documents'):
    return f"{reverse('order_detail', args=[order.pk])}#{anchor}"


def _redirect_back(request, fallback):
    return redirect(_safe_redirect_url(request, request.POST.get('next', ''), fallback))


def _flash_form_errors(request, form):
    for errors in form.errors.values():
        for error in errors:
            messages.error(request, error)


def _uses_presigned_urls(storage):
    """S3-style storages hand out short-lived signed URLs instead of local paths."""
    return hasattr(storage, 'bucket_name')


def _private_file_response(full_path, internal_uri, content_type, disposition):
    """Serve a file that must stay behind login.

    In production nginx streams it from an ``internal`` location via
    X-Accel-Redirect; in development Django serves it directly.
    """
    if settings.DEBUG:
        if not os.path.exists(full_path):
            return HttpResponse('الملف غير موجود', status=404)
        response = FileResponse(open(full_path, 'rb'), content_type=content_type)
    else:
        response = HttpResponse(content_type=content_type)
        response['X-Accel-Redirect'] = internal_uri
    response['Content-Disposition'] = disposition
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@login_required
def dashboard(request):
    profile = _get_profile(request.user)
    context = {
        'profile': profile,
        'contract_total': settings.CONTRACT_TOTAL_VALUE,
        'total_invoiced': CompletionCertificate.total_invoiced(),
    }
    context['remaining'] = context['contract_total'] - context['total_invoiced']
    if context['contract_total'] > 0:
        context['usage_percent'] = int(
            context['total_invoiced'] / context['contract_total'] * 100
        )
    else:
        context['usage_percent'] = 0

    # Stats for summary cards
    context['stats'] = {
        'total': WorkOrder.objects.exclude(status=WorkOrder.Status.DRAFT).count(),
        'active': WorkOrder.objects.filter(status__in=[
            WorkOrder.Status.SUBMITTED, WorkOrder.Status.ACCEPTED,
            WorkOrder.Status.ASSIGNED, WorkOrder.Status.IN_PROGRESS,
        ]).count(),
        'pending': WorkOrder.objects.filter(status=WorkOrder.Status.PENDING_APPROVAL).count(),
        'completed': WorkOrder.objects.filter(status=WorkOrder.Status.COMPLETED).count(),
    }

    if request.user.is_superuser:
        context['all_orders'] = WorkOrder.objects.all()[:20]
        context['incoming_orders'] = WorkOrder.objects.filter(
            status=WorkOrder.Status.SUBMITTED
        )[:10]
        context['to_log'] = WorkOrder.objects.filter(
            status=WorkOrder.Status.ACCEPTED
        )[:10]
        context['pending_approvals'] = WorkOrder.objects.filter(
            status=WorkOrder.Status.PENDING_APPROVAL
        )[:10]
    elif profile and profile.is_pp_staff:
        context['my_orders'] = WorkOrder.objects.filter(
            created_by=request.user
        ).exclude(status=WorkOrder.Status.DRAFT)[:10]
        context['pending_approval'] = WorkOrder.objects.filter(
            status=WorkOrder.Status.PENDING_APPROVAL,
            prosecution=profile.prosecution,
        )[:10]
    elif profile and profile.is_smartworld:
        context['incoming_orders'] = WorkOrder.objects.filter(
            status=WorkOrder.Status.SUBMITTED
        )[:10]
        context['to_log'] = WorkOrder.objects.filter(
            status=WorkOrder.Status.ACCEPTED
        )[:10]
        context['pending_approvals'] = WorkOrder.objects.filter(
            status=WorkOrder.Status.PENDING_APPROVAL
        )[:10]
    elif profile and profile.is_contract_manager:
        context['all_orders'] = WorkOrder.objects.all()[:20]
        context['incoming_orders'] = WorkOrder.objects.filter(
            status=WorkOrder.Status.SUBMITTED
        )[:10]
        context['to_assign'] = WorkOrder.objects.filter(
            status=WorkOrder.Status.ACCEPTED
        )[:10]
        context['pending_approvals'] = WorkOrder.objects.filter(
            status=WorkOrder.Status.PENDING_APPROVAL
        )[:10]
    elif profile and profile.is_translator:
        context.update(_translator_assignments(request.user, limit=10))
        context['workflow_mode'] = WorkflowConfig.get_config().mode

    recent_statuses = [
        WorkOrder.Status.SUBMITTED, WorkOrder.Status.ACCEPTED,
        WorkOrder.Status.ASSIGNED, WorkOrder.Status.PENDING_APPROVAL,
        WorkOrder.Status.COMPLETED,
    ]
    context['recent_activity'] = _visible_orders(request.user, profile).filter(
        status__in=recent_statuses
    ).order_by('-updated_at')[:10]

    return render(request, 'dashboard.html', context)


@login_required
def order_list(request):
    profile = _get_profile(request.user)
    orders = _visible_orders(request.user, profile)

    status_filter = request.GET.get('status')
    if status_filter:
        orders = orders.filter(status=status_filter)

    return render(request, 'orders/list.html', {
        'orders': orders,
        'profile': profile,
        'status_choices': WorkOrder.Status.choices,
        'current_status': status_filter,
    })


@login_required
def order_create(request):
    profile = _get_profile(request.user)
    if not request.user.is_superuser and (not profile or not profile.is_pp_staff):
        return HttpResponseForbidden('غير مسموح')

    if request.method == 'POST':
        form = WorkOrderForm(request.POST, request.FILES, user=request.user)
        formset = WorkOrderLanguageFormSet(request.POST)
        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                order = form.save(commit=False)
                order.created_by = request.user
                order.status = WorkOrder.Status.SUBMITTED
                order.submitted_at = timezone.now()
                order.save()
                formset.instance = order
                formset.save()
                _save_documents(
                    order, form.cleaned_data['source_files'],
                    OrderDocument.Kind.SOURCE, request.user,
                )
            notify_new_order(order)
            messages.success(request, f'تم تقديم أمر التكليف رقم {order.order_number} بنجاح')
            return redirect('order_detail', pk=order.pk)
    else:
        form = WorkOrderForm(user=request.user)
        formset = WorkOrderLanguageFormSet()

    languages = Language.objects.all()
    rate_data = {str(lang.pk): {'hourly': str(lang.hourly_rate), 'page': str(lang.page_rate)} for lang in languages}

    return render(request, 'orders/create.html', {
        'form': form,
        'formset': formset,
        'profile': profile,
        'rate_data': rate_data,
        'allowed_extensions': _accept_attribute(),
        'max_upload_size': settings.DOCUMENT_MAX_UPLOAD_SIZE,
    })


@login_required
def order_detail(request, pk):
    order = get_object_or_404(WorkOrder, pk=pk)
    profile = _get_profile(request.user)
    if not _can_view_order(request.user, profile, order):
        return HttpResponseForbidden('غير مسموح')

    meeting_link_form = None
    if ((request.user.is_superuser or (profile and profile.is_smartworld))
            and order.location_type == WorkOrder.LocationType.NEED_LINK
            and order.status in (WorkOrder.Status.SUBMITTED, WorkOrder.Status.ACCEPTED)):
        meeting_link_form = MeetingLinkForm()

    assignments = order.assignments.select_related(
        'translator', 'language_line', 'language_line__language'
    ).all()
    config = WorkflowConfig.get_config()
    recordings = order.recordings.filter(
        status=ConferenceRecording.Status.COMPLETED
    )

    return render(request, 'orders/detail.html', {
        'order': order,
        'profile': profile,
        'meeting_link_form': meeting_link_form,
        'assignments': assignments,
        'workflow_mode': config.mode,
        'recordings': recordings,
        **_document_context(request, order, profile),
        **_glossary_order_context(request, order),
    })


@login_required
@require_POST
def order_accept(request, pk):
    order = get_object_or_404(WorkOrder, pk=pk)
    profile = _get_profile(request.user)
    if not request.user.is_superuser and (
        not profile or not (profile.is_smartworld or profile.is_contract_manager)
    ):
        return HttpResponseForbidden('غير مسموح')
    if order.status != WorkOrder.Status.SUBMITTED:
        messages.error(request, 'لا يمكن قبول هذا الأمر')
        return redirect('order_detail', pk=pk)

    order.status = WorkOrder.Status.ACCEPTED
    order.save()
    notify_order_accepted(order)
    messages.success(request, 'تم قبول أمر التكليف')
    return redirect('order_detail', pk=pk)


@login_required
def order_provide_link(request, pk):
    order = get_object_or_404(WorkOrder, pk=pk)
    profile = _get_profile(request.user)
    if not request.user.is_superuser and (not profile or not profile.is_smartworld):
        return HttpResponseForbidden('غير مسموح')

    if request.method == 'POST':
        form = MeetingLinkForm(request.POST)
        if form.is_valid():
            order.location_detail = form.cleaned_data['meeting_link']
            order.location_type = WorkOrder.LocationType.ONLINE
            order.save()
            notify_meeting_link(order)
            messages.success(request, 'تم إضافة رابط الاجتماع')
    return redirect('order_detail', pk=pk)


@login_required
def order_log_service(request, pk):
    order = get_object_or_404(WorkOrder, pk=pk)
    profile = _get_profile(request.user)
    if not request.user.is_superuser and (not profile or not profile.is_smartworld):
        return HttpResponseForbidden('غير مسموح')
    if order.status not in (
        WorkOrder.Status.ACCEPTED, WorkOrder.Status.ASSIGNED,
        WorkOrder.Status.IN_PROGRESS, WorkOrder.Status.DISPUTED,
    ):
        messages.error(request, 'لا يمكن تسجيل الخدمة لهذا الأمر')
        return redirect('order_detail', pk=pk)

    lang_lines = order.languages.select_related('language').all()

    if request.method == 'POST':
        # Records a translator logged stay linked to their assignment and are
        # corrected in place; deleting them would orphan the assignment.
        linked_records = {
            assignment.language_line_id: assignment.service_record
            for assignment in order.assignments.filter(
                service_record__isnull=False
            ).select_related('service_record')
        }
        line_forms = []
        for lang_line in lang_lines:
            prefix = f'lang_{lang_line.pk}'
            record = linked_records.get(lang_line.pk) or ServiceRecord(
                work_order=order, language=lang_line.language,
            )
            form = ServiceRecordForm({
                'actual_hours': request.POST.get(f'{prefix}_hours', ''),
                'actual_pages': request.POST.get(f'{prefix}_pages', ''),
                'num_translators': request.POST.get(
                    f'{prefix}_translators', lang_line.num_translators
                ),
                'notes': request.POST.get(f'{prefix}_notes', ''),
            }, instance=record)
            line_forms.append((lang_line, form))

        invalid = [(ll, form) for ll, form in line_forms if not form.is_valid()]
        for lang_line, form in invalid:
            errors = '، '.join(e for errs in form.errors.values() for e in errs)
            messages.error(request, f'{lang_line.language_display}: {errors}')

        if not invalid:
            with transaction.atomic():
                kept = []
                for _, form in line_forms:
                    record = form.save(commit=False)
                    record.calculate_amount()
                    record.save()
                    kept.append(record.pk)
                order.service_records.exclude(pk__in=kept).delete()

                approval, _ = WorkOrderApproval.objects.get_or_create(work_order=order)
                approval.smartworld_approved_by = request.user
                approval.smartworld_approved_at = timezone.now()
                approval.save()

                order.status = WorkOrder.Status.PENDING_APPROVAL
                order.save()

            notify_actuals_logged(order)
            messages.success(request, 'تم تسجيل الخدمة الفعلية وإرسالها للاعتماد')
            return redirect('order_detail', pk=pk)

    return render(request, 'orders/log_service.html', {
        'order': order,
        'lang_lines': lang_lines,
        'profile': profile,
    })


@login_required
def order_approve(request, pk):
    order = get_object_or_404(WorkOrder, pk=pk)
    profile = _get_profile(request.user)
    # PP staff may only approve orders of their own prosecution (or their own).
    if not request.user.is_superuser and (
        not profile or not profile.is_pp_staff
        or not _can_view_order(request.user, profile, order)
    ):
        return HttpResponseForbidden('غير مسموح')
    if order.status != WorkOrder.Status.PENDING_APPROVAL:
        messages.error(request, 'لا يمكن اعتماد هذا الأمر')
        return redirect('order_detail', pk=pk)

    lang_lines = order.languages.select_related('language').all()
    service_records = order.service_records.select_related('language').all()
    dispute_form = DisputeForm()

    if request.method == 'POST':
        action = request.POST.get('action')
        approval = order.approval

        if action == 'approve':
            open_notes = order.document_notes.filter(status=DocumentNote.Status.OPEN).count()
            if open_notes:
                messages.error(
                    request,
                    f'لا يمكن الاعتماد: توجد {open_notes} ملاحظة مراجعة بانتظار معالجة المترجم',
                )
                return redirect('order_approve', pk=pk)
            approval.pp_approved_by = request.user
            approval.pp_approved_at = timezone.now()
            approval.save()
            order.status = WorkOrder.Status.COMPLETED
            order.save()

            # Auto-generate completion certificate
            subtotal = order.service_records.aggregate(
                total=Sum('amount')
            )['total'] or Decimal('0')
            CompletionCertificate.objects.create(
                work_order=order,
                subtotal=subtotal,
            )

            notify_pp_approved(order)
            messages.success(request, 'تم اعتماد أمر التكليف وإنشاء شهادة الإنجاز')
            return redirect('order_certificate', pk=pk)

        elif action == 'dispute':
            dispute_form = DisputeForm(request.POST)
            if dispute_form.is_valid():
                approval.pp_dispute_reason = dispute_form.cleaned_data['reason']
                approval.save()
                order.status = WorkOrder.Status.DISPUTED
                order.save()
                notify_pp_disputed(order)
                messages.warning(request, 'تم الاعتراض على أمر التكليف')
                return redirect('order_detail', pk=pk)

    return render(request, 'orders/approve.html', {
        'order': order,
        'lang_lines': lang_lines,
        'service_records': service_records,
        'dispute_form': dispute_form,
        'profile': profile,
        **_document_context(request, order, profile),
        **_glossary_order_context(request, order),
    })


@login_required
def order_certificate(request, pk):
    order = get_object_or_404(WorkOrder, pk=pk)
    profile = _get_profile(request.user)
    if not _can_view_order(request.user, profile, order):
        return HttpResponseForbidden('غير مسموح')
    try:
        certificate = order.certificate
    except CompletionCertificate.DoesNotExist:
        messages.error(request, 'لا توجد شهادة إنجاز لهذا الأمر')
        return redirect('order_detail', pk=pk)

    service_records = order.service_records.select_related('language').all()
    return render(request, 'orders/certificate.html', {
        'order': order,
        'certificate': certificate,
        'service_records': service_records,
        'contract_number': settings.CONTRACT_NUMBER,
        'profile': profile,
        'can_finalize': _can_finalize_certificate(request.user, profile),
    })


@login_required
@require_POST
def certificate_finalize(request, pk):
    """SmartWorld admin marks a certificate as invoiced.

    Only finalized certificates count against the contract balance.
    """
    order = get_object_or_404(WorkOrder, pk=pk)
    if not _can_finalize_certificate(request.user, _get_profile(request.user)):
        return HttpResponseForbidden('غير مسموح')
    certificate = get_object_or_404(CompletionCertificate, work_order=order)

    if certificate.status == CompletionCertificate.Status.FINALIZED:
        messages.info(request, 'الشهادة معتمدة نهائياً بالفعل')
    else:
        certificate.status = CompletionCertificate.Status.FINALIZED
        certificate.save()
        messages.success(request, 'تم الاعتماد النهائي للشهادة واحتسابها في رصيد العقد')
    return redirect('order_certificate', pk=pk)


@login_required
def order_pdf(request, pk):
    order = get_object_or_404(WorkOrder, pk=pk)
    if not _can_view_order(request.user, _get_profile(request.user), order):
        return HttpResponseForbidden('غير مسموح')
    lang_lines = order.languages.select_related('language').all()
    import os, base64
    static_dir = settings.STATICFILES_DIRS[0] if settings.STATICFILES_DIRS else settings.STATIC_ROOT
    pp_logo_file = os.path.join(static_dir, 'images', 'pp_logo.png')
    uae_emblem_file = os.path.join(static_dir, 'images', 'uae_emblem.png')
    with open(pp_logo_file, 'rb') as f:
        pp_logo_b64 = base64.b64encode(f.read()).decode()
    with open(uae_emblem_file, 'rb') as f:
        uae_emblem_b64 = base64.b64encode(f.read()).decode()
    context = {
        'order': order,
        'lang_lines': lang_lines,
        'contract_number': settings.CONTRACT_NUMBER,
        'pp_logo_data': f'data:image/png;base64,{pp_logo_b64}',
        'uae_emblem_data': f'data:image/png;base64,{uae_emblem_b64}',
    }
    html = render(request, 'pdf/work_order.html', context).content.decode('utf-8')

    try:
        from weasyprint import HTML
        pdf = HTML(string=html, base_url=request.build_absolute_uri('/')).write_pdf()
        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = f'filename="work_order_{order.order_number.replace("/", "_")}.pdf"'
        return response
    except ImportError:
        return HttpResponse(html)


@login_required
def certificate_pdf(request, pk):
    order = get_object_or_404(WorkOrder, pk=pk)
    if not _can_view_order(request.user, _get_profile(request.user), order):
        return HttpResponseForbidden('غير مسموح')
    try:
        certificate = order.certificate
    except CompletionCertificate.DoesNotExist:
        return HttpResponse('لا توجد شهادة', status=404)

    import os, base64
    static_dir = settings.STATICFILES_DIRS[0] if settings.STATICFILES_DIRS else settings.STATIC_ROOT
    pp_logo_file = os.path.join(static_dir, 'images', 'pp_logo.png')
    uae_emblem_file = os.path.join(static_dir, 'images', 'uae_emblem.png')
    with open(pp_logo_file, 'rb') as f:
        pp_logo_b64 = base64.b64encode(f.read()).decode()
    with open(uae_emblem_file, 'rb') as f:
        uae_emblem_b64 = base64.b64encode(f.read()).decode()
    service_records = order.service_records.select_related('language').all()
    context = {
        'order': order,
        'certificate': certificate,
        'service_records': service_records,
        'contract_number': settings.CONTRACT_NUMBER,
        'pp_logo_data': f'data:image/png;base64,{pp_logo_b64}',
        'uae_emblem_data': f'data:image/png;base64,{uae_emblem_b64}',
    }
    html = render(request, 'pdf/certificate.html', context).content.decode('utf-8')

    try:
        from weasyprint import HTML
        pdf = HTML(string=html, base_url=request.build_absolute_uri('/')).write_pdf()
        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = f'filename="certificate_{certificate.certificate_number.replace("/", "_")}.pdf"'
        return response
    except ImportError:
        return HttpResponse(html)


@login_required
@require_POST
def cm_accept_order(request, pk):
    """Contract manager accepts a submitted order"""
    order = get_object_or_404(WorkOrder, pk=pk)
    profile = _get_profile(request.user)
    if not request.user.is_superuser and (not profile or not profile.is_contract_manager):
        return HttpResponseForbidden('غير مسموح')
    if order.status != WorkOrder.Status.SUBMITTED:
        messages.error(request, 'لا يمكن قبول هذا الأمر')
        return redirect('order_detail', pk=pk)

    order.status = WorkOrder.Status.ACCEPTED
    order.save()
    notify_order_accepted(order)
    messages.success(request, 'تم قبول أمر التكليف')
    return redirect('order_detail', pk=pk)


@login_required
def cm_assign_translators(request, pk):
    """Contract manager assigns translators to language lines"""
    order = get_object_or_404(WorkOrder, pk=pk)
    profile = _get_profile(request.user)
    if not request.user.is_superuser and (not profile or not profile.is_contract_manager):
        return HttpResponseForbidden('غير مسموح')
    if order.status not in (WorkOrder.Status.ACCEPTED, WorkOrder.Status.ASSIGNED):
        messages.error(request, 'لا يمكن تعيين مترجمين لهذا الأمر')
        return redirect('order_detail', pk=pk)

    lang_lines = order.languages.select_related('language').all()
    forms_list = []
    for ll in lang_lines:
        existing = OrderAssignment.objects.filter(
            language_line=ll
        ).exclude(status=OrderAssignment.Status.DECLINED).first()
        form = AssignTranslatorForm(
            request.POST or None,
            language_line=ll,
            service_type=order.service_type,
            prefix=f'll_{ll.pk}',
        )
        forms_list.append({
            'lang_line': ll,
            'form': form,
            'existing': existing,
        })

    if request.method == 'POST':
        new_assignments = []
        for item in forms_list:
            if item['existing']:
                continue
            if item['form'].is_valid():
                translator = item['form'].cleaned_data['translator']
                assignment = OrderAssignment.objects.create(
                    work_order=order,
                    language_line=item['lang_line'],
                    translator=translator,
                )
                new_assignments.append(assignment)

        if new_assignments:
            # Check if all language lines now have assignments
            all_assigned = True
            for ll in lang_lines:
                has_active = OrderAssignment.objects.filter(
                    language_line=ll
                ).exclude(status=OrderAssignment.Status.DECLINED).exists()
                if not has_active:
                    all_assigned = False
                    break

            if all_assigned:
                order.status = WorkOrder.Status.ASSIGNED
                order.save()

            for assignment in new_assignments:
                notify_translator_assigned(assignment)

            messages.success(request, f'تم تعيين {len(new_assignments)} مترجم(ين)')
            return redirect('order_detail', pk=pk)

    return render(request, 'orders/assign.html', {
        'order': order,
        'forms_list': forms_list,
        'profile': profile,
    })


@login_required
def translator_dashboard(request):
    """Translator's own dashboard showing assignments"""
    profile = _get_profile(request.user)
    if not request.user.is_superuser and (not profile or not profile.is_translator):
        return HttpResponseForbidden('غير مسموح')

    return render(request, 'orders/translator_dashboard.html', {
        **_translator_assignments(request.user),
        'workflow_mode': WorkflowConfig.get_config().mode,
        'profile': profile,
    })


@login_required
def translator_available_orders(request):
    """AUTO mode: show orders matching translator's languages"""
    profile = _get_profile(request.user)
    if not request.user.is_superuser and (not profile or not profile.is_translator):
        return HttpResponseForbidden('غير مسموح')

    config = WorkflowConfig.get_config()
    if config.mode != WorkflowConfig.Mode.AUTO:
        messages.info(request, 'التعيين التلقائي غير مفعّل حالياً')
        return redirect('translator_dashboard')

    try:
        translator_profile = request.user.translator_profile
    except TranslatorProfile.DoesNotExist:
        messages.error(request, 'لا يوجد ملف مترجم مرتبط بحسابك')
        return redirect('translator_dashboard')

    my_languages = translator_profile.languages.all()
    # Find ACCEPTED orders with language lines matching translator's languages
    # that don't already have an active assignment
    available_lines = WorkOrderLanguage.objects.filter(
        work_order__status__in=[WorkOrder.Status.ACCEPTED, WorkOrder.Status.ASSIGNED],
        language__in=my_languages,
    ).exclude(
        assignments__status__in=[
            OrderAssignment.Status.PENDING,
            OrderAssignment.Status.ACCEPTED,
            OrderAssignment.Status.COMPLETED,
        ]
    ).select_related('work_order', 'language')

    # Filter by service type capability
    filtered = []
    for ll in available_lines:
        if ll.work_order.is_interpretation and translator_profile.can_interpret:
            filtered.append(ll)
        elif ll.work_order.is_written and translator_profile.can_translate:
            filtered.append(ll)

    return render(request, 'orders/available_orders.html', {
        'available_lines': filtered,
        'profile': profile,
    })


@login_required
@require_POST
def translator_self_assign(request, ll_pk):
    """AUTO mode: translator self-assigns to a language line"""
    profile = _get_profile(request.user)
    if not request.user.is_superuser and (not profile or not profile.is_translator):
        return HttpResponseForbidden('غير مسموح')

    config = WorkflowConfig.get_config()
    if config.mode != WorkflowConfig.Mode.AUTO:
        messages.error(request, 'التعيين التلقائي غير مفعّل')
        return redirect('translator_dashboard')

    lang_line = get_object_or_404(WorkOrderLanguage, pk=ll_pk)
    order = lang_line.work_order

    if order.status not in (WorkOrder.Status.ACCEPTED, WorkOrder.Status.ASSIGNED):
        messages.error(request, 'لا يمكن التقديم على هذا الأمر')
        return redirect('translator_available_orders')

    # Check not already assigned
    existing = OrderAssignment.objects.filter(
        language_line=lang_line
    ).exclude(status=OrderAssignment.Status.DECLINED).exists()
    if existing:
        messages.error(request, 'هذا البند معين بالفعل لمترجم آخر')
        return redirect('translator_available_orders')

    assignment = OrderAssignment.objects.create(
        work_order=order,
        language_line=lang_line,
        translator=request.user,
        status=OrderAssignment.Status.ACCEPTED,
        accepted_at=timezone.now(),
    )

    # Check if all lines assigned
    all_assigned = True
    for ll in order.languages.all():
        has_active = OrderAssignment.objects.filter(
            language_line=ll
        ).exclude(status=OrderAssignment.Status.DECLINED).exists()
        if not has_active:
            all_assigned = False
            break
    if all_assigned and order.status != WorkOrder.Status.ASSIGNED:
        order.status = WorkOrder.Status.ASSIGNED
        order.save()

    messages.success(request, f'تم تقديم طلبك للعمل على {lang_line.language_display}')
    return redirect('translator_dashboard')


@login_required
@require_POST
def assignment_accept(request, pk):
    """Translator accepts their assignment"""
    assignment = get_object_or_404(OrderAssignment, pk=pk)
    if assignment.translator != request.user and not request.user.is_superuser:
        return HttpResponseForbidden('غير مسموح')
    if assignment.status != OrderAssignment.Status.PENDING:
        messages.error(request, 'لا يمكن قبول هذا التعيين')
        return redirect('translator_dashboard')

    assignment.status = OrderAssignment.Status.ACCEPTED
    assignment.accepted_at = timezone.now()
    assignment.save()
    notify_assignment_accepted(assignment)
    messages.success(request, 'تم قبول التعيين بنجاح')
    return redirect('translator_dashboard')


@login_required
@require_POST
def assignment_decline(request, pk):
    """Translator declines their assignment"""
    assignment = get_object_or_404(OrderAssignment, pk=pk)
    if assignment.translator != request.user and not request.user.is_superuser:
        return HttpResponseForbidden('غير مسموح')
    if assignment.status != OrderAssignment.Status.PENDING:
        messages.error(request, 'لا يمكن رفض هذا التعيين')
        return redirect('translator_dashboard')

    assignment.status = OrderAssignment.Status.DECLINED
    assignment.save()
    notify_assignment_declined(assignment)
    messages.warning(request, 'تم رفض التعيين')
    return redirect('translator_dashboard')


@login_required
def translator_log_service(request, pk):
    """Translator logs service for their specific assignment"""
    assignment = get_object_or_404(
        OrderAssignment.objects.select_related(
            'work_order', 'language_line', 'language_line__language'
        ),
        pk=pk,
    )
    if assignment.translator != request.user and not request.user.is_superuser:
        return HttpResponseForbidden('غير مسموح')
    if assignment.status != OrderAssignment.Status.ACCEPTED:
        messages.error(request, 'لا يمكن تسجيل الخدمة لهذا التعيين')
        return redirect('translator_dashboard')

    order = assignment.work_order
    lang_line = assignment.language_line
    has_translation = lang_line.documents.filter(
        kind=OrderDocument.Kind.TRANSLATION, purged_at__isnull=True,
    ).exists()
    is_post = request.method == 'POST'
    form = TranslatorServiceForm(
        request.POST if is_post else None,
        request.FILES if is_post else None,
        require_translation=order.is_written,
        has_existing_translation=has_translation,
        order=order,
    )

    if is_post and form.is_valid():
        documents, addressed = [], 0
        with transaction.atomic():
            # Create ServiceRecord
            record = ServiceRecord(
                work_order=order,
                language=lang_line.language,
                num_translators=1,
                notes=form.cleaned_data.get('notes', ''),
            )
            if form.cleaned_data.get('actual_hours'):
                record.actual_hours = form.cleaned_data['actual_hours']
            if form.cleaned_data.get('actual_pages'):
                record.actual_pages = form.cleaned_data['actual_pages']
            record.calculate_amount()
            record.save()

            if form.cleaned_data['translation_files']:
                documents, addressed = _save_translation(
                    order, lang_line, form.cleaned_data['translation_files'], request.user,
                    assignment=assignment, note=form.cleaned_data.get('notes', ''),
                )

            # Link record to assignment and mark completed
            assignment.service_record = record
            assignment.status = OrderAssignment.Status.COMPLETED
            assignment.completed_at = timezone.now()
            assignment.save()

        if documents:
            notify_translation_uploaded(
                order, lang_line, documents, request.user, addressed_count=addressed,
            )

        # Check if ALL assignments for this order are completed
        total_active = order.assignments.exclude(
            status=OrderAssignment.Status.DECLINED
        ).count()
        total_completed = order.assignments.filter(
            status=OrderAssignment.Status.COMPLETED
        ).count()

        if total_active > 0 and total_completed == total_active:
            # Create approval record
            approval, _ = WorkOrderApproval.objects.get_or_create(work_order=order)
            approval.smartworld_approved_by = request.user
            approval.smartworld_approved_at = timezone.now()
            approval.save()

            order.status = WorkOrder.Status.PENDING_APPROVAL
            order.save()
            notify_all_assignments_completed(order)
            notify_actuals_logged(order)

        messages.success(request, 'تم تسجيل الخدمة بنجاح')
        return redirect('translator_dashboard')

    return render(request, 'orders/translator_log.html', {
        'assignment': assignment,
        'order': order,
        'lang_line': lang_line,
        'form': form,
        'profile': _get_profile(request.user),
        'source_documents': order.documents.filter(kind=OrderDocument.Kind.SOURCE),
        'line_notes': lang_line.document_notes.select_related('author', 'document'),
        'has_translation': has_translation,
        'allowed_extensions': _accept_attribute(),
    })


@login_required
def prosecutors_by_prosecution(request):
    """HTMX endpoint: returns prosecutor options for a prosecution"""
    prosecution_id = request.GET.get('prosecution')
    if prosecution_id:
        prosecutors = Prosecutor.objects.filter(
            prosecution_id=prosecution_id, is_active=True
        )
    else:
        prosecutors = Prosecutor.objects.none()

    options = format_html_join(
        '', '<option value="{}">{}</option>',
        ((p.pk, p.name) for p in prosecutors),
    )
    return HttpResponse('<option value="">---------</option>' + options)


# ── LiveKit Conference Views ──────────────────────────────────────────

def _can_manage_conference(user, profile):
    """Check if user can create conference rooms."""
    if user.is_superuser:
        return True
    if profile and (profile.is_smartworld_admin or profile.is_contract_manager):
        return True
    return False


def _can_join_conference(user, profile, order):
    """Check if user is linked to the order and can join its conference."""
    if user.is_superuser:
        return True
    if profile and (profile.is_smartworld_admin or profile.is_smartworld
                    or profile.is_contract_manager):
        return True
    if order.created_by == user:
        return True
    if profile and profile.is_pp_staff and profile.prosecution == order.prosecution:
        return True
    if order.assignments.filter(translator=user).exists():
        return True
    return False


@login_required
@require_POST
def conference_create(request, pk):
    """Create/start a LiveKit conference room for an online order."""
    order = get_object_or_404(WorkOrder, pk=pk)
    profile = _get_profile(request.user)

    if not _can_manage_conference(request.user, profile):
        return HttpResponseForbidden('غير مسموح')

    if order.conference_room:
        return redirect('conference_join', pk=pk)

    # Generate room name from order number: 2026/AO/0001 → pp-2026-AO-0001
    room_name = 'pp-' + order.order_number.replace('/', '-')
    result = create_room(room_name)

    if result:
        order.conference_room = room_name
        join_url = request.build_absolute_uri(f'/orders/{order.pk}/conference/')
        if not order.location_detail:
            order.location_detail = join_url
            order.location_type = WorkOrder.LocationType.ONLINE
        order.save()
        messages.success(request, 'تم إنشاء غرفة الاجتماع بنجاح')
        return redirect('conference_join', pk=pk)
    else:
        messages.error(request, 'فشل إنشاء غرفة الاجتماع. يرجى المحاولة لاحقاً.')
        return redirect('order_detail', pk=pk)


@login_required
def conference_join(request, pk):
    """Render the video conference room page."""
    order = get_object_or_404(WorkOrder, pk=pk)
    profile = _get_profile(request.user)

    if not _can_join_conference(request.user, profile, order):
        return HttpResponseForbidden('غير مسموح')

    if not order.conference_room:
        messages.error(request, 'لم يتم إنشاء غرفة اجتماع لهذا الأمر بعد')
        return redirect('order_detail', pk=pk)

    participant_name = request.user.get_full_name() or request.user.username
    participant_identity = request.user.email or request.user.username
    token = generate_join_token(
        order.conference_room, participant_name, participant_identity,
    )

    can_manage = _can_manage_conference(request.user, profile)

    return render(request, 'orders/conference.html', {
        'order': order,
        'ws_url': settings.LIVEKIT_WS_URL,
        'token': token,
        'room_name': order.conference_room,
        'profile': profile,
        'can_manage': can_manage,
    })


@login_required
def conference_token_api(request, pk):
    """JSON endpoint returning a fresh join token (for reconnection)."""
    order = get_object_or_404(WorkOrder, pk=pk)
    profile = _get_profile(request.user)

    if not _can_join_conference(request.user, profile, order):
        return JsonResponse({'error': 'غير مسموح'}, status=403)

    if not order.conference_room:
        return JsonResponse({'error': 'لا توجد غرفة اجتماع'}, status=404)

    participant_name = request.user.get_full_name() or request.user.username
    participant_identity = request.user.email or request.user.username
    token = generate_join_token(
        order.conference_room, participant_name, participant_identity,
    )
    return JsonResponse({'token': token})


# ── Recording Views ──────────────────────────────────────────────────

@login_required
def recording_start(request, pk):
    """Start recording the conference session."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    order = get_object_or_404(WorkOrder, pk=pk)
    profile = _get_profile(request.user)

    if not _can_manage_conference(request.user, profile):
        return JsonResponse({'error': 'غير مسموح'}, status=403)

    if not order.conference_room:
        return JsonResponse({'error': 'لا توجد غرفة اجتماع'}, status=404)

    # Check no active recording exists
    active = order.recordings.filter(
        status__in=[ConferenceRecording.Status.RECORDING,
                    ConferenceRecording.Status.STOPPING]
    ).exists()
    if active:
        return JsonResponse({'error': 'يوجد تسجيل نشط بالفعل'}, status=409)

    result = start_room_recording(order.conference_room)
    if not result:
        return JsonResponse({'error': 'فشل بدء التسجيل'}, status=502)

    recording = ConferenceRecording.objects.create(
        work_order=order,
        egress_id=result['egress_id'],
        room_name=order.conference_room,
        file_path=result['file_path'],
        started_by=request.user,
    )

    return JsonResponse({
        'ok': True,
        'recording_id': recording.pk,
        'egress_id': recording.egress_id,
    })


@login_required
def recording_stop(request, pk):
    """Stop the active conference recording."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    order = get_object_or_404(WorkOrder, pk=pk)
    profile = _get_profile(request.user)

    if not _can_manage_conference(request.user, profile):
        return JsonResponse({'error': 'غير مسموح'}, status=403)

    recording = order.recordings.filter(
        status=ConferenceRecording.Status.RECORDING
    ).first()
    if not recording:
        return JsonResponse({'error': 'لا يوجد تسجيل نشط'}, status=404)

    success = stop_recording(recording.egress_id)
    if not success:
        return JsonResponse({'error': 'فشل إيقاف التسجيل'}, status=502)

    recording.status = ConferenceRecording.Status.STOPPING
    recording.stopped_at = timezone.now()
    recording.save()

    return JsonResponse({'ok': True})


@login_required
def recording_status(request, pk):
    """Return the current recording status (polled by all participants)."""
    order = get_object_or_404(WorkOrder, pk=pk)
    profile = _get_profile(request.user)

    if not _can_join_conference(request.user, profile, order):
        return JsonResponse({'error': 'غير مسموح'}, status=403)

    recording = order.recordings.filter(
        status__in=[ConferenceRecording.Status.RECORDING,
                    ConferenceRecording.Status.STOPPING]
    ).first()

    if not recording:
        return JsonResponse({'recording': False})

    # For STOPPING recordings, check egress API to detect completion
    if recording.status == ConferenceRecording.Status.STOPPING:
        items = list_egress(order.conference_room)
        still_active = any(
            item.get('egress_id') == recording.egress_id
            for item in items
        )
        if not still_active:
            recording.status = ConferenceRecording.Status.COMPLETED
            # Try to get file size
            import os
            full_path = os.path.join(
                settings.RECORDING_ROOT, recording.file_path
            )
            if os.path.exists(full_path):
                recording.file_size = os.path.getsize(full_path)
            recording.save()
            return JsonResponse({'recording': False})

    return JsonResponse({
        'recording': True,
        'status': recording.status,
        'started_at': recording.started_at.isoformat(),
    })


@login_required
def recording_download(request, pk, rec_pk):
    """Download a completed recording file."""
    import os
    order = get_object_or_404(WorkOrder, pk=pk)
    profile = _get_profile(request.user)

    if not _can_join_conference(request.user, profile, order):
        return HttpResponseForbidden('غير مسموح')

    recording = get_object_or_404(
        ConferenceRecording, pk=rec_pk, work_order=order,
        status=ConferenceRecording.Status.COMPLETED,
    )

    return _private_file_response(
        os.path.join(settings.RECORDING_ROOT, recording.file_path),
        f'/internal-recordings/{recording.file_path}',
        'video/mp4',
        content_disposition_header(True, os.path.basename(recording.file_path)),
    )


# ── Captions Views ───────────────────────────────────────────────────

@login_required
def captions_toggle(request, pk):
    """Toggle live captions room-wide via room metadata (manager only)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    order = get_object_or_404(WorkOrder, pk=pk)
    profile = _get_profile(request.user)

    if not _can_manage_conference(request.user, profile):
        return JsonResponse({'error': 'غير مسموح'}, status=403)

    if not order.conference_room:
        return JsonResponse({'error': 'لا توجد غرفة اجتماع'}, status=404)

    import json
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    enable = bool(body.get('enable', False))
    success = update_room_metadata(order.conference_room, {'captions': enable})

    if not success:
        return JsonResponse({'error': 'فشل تحديث الغرفة'}, status=502)

    return JsonResponse({'ok': True, 'captions': enable})


@login_required
def captions_translate(request, pk):
    """Translate a caption text using OpenAI GPT-4o-mini."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    order = get_object_or_404(WorkOrder, pk=pk)
    profile = _get_profile(request.user)

    if not _can_join_conference(request.user, profile, order):
        return JsonResponse({'error': 'غير مسموح'}, status=403)

    if not settings.OPENAI_API_KEY:
        return JsonResponse({'error': 'Translation not configured'}, status=503)

    import json
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    text = body.get('text', '').strip()
    source_lang = body.get('source_lang', '')
    target_lang = body.get('target_lang', '')

    if not text or not target_lang:
        return JsonResponse({'error': 'Missing text or target_lang'}, status=400)

    from openai import OpenAI
    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    system_prompt = (
        'You are a legal translation assistant for UAE Federal Prosecution. '
        'Translate the following text accurately and concisely. '
        'Preserve legal terminology. Return ONLY the translated text, '
        'no explanations or notes.'
    ) + glossary_prompt(text, source_lang, target_lang)

    try:
        resp = client.chat.completions.create(
            model='gpt-4o-mini',
            temperature=0.1,
            max_tokens=500,
            messages=[
                {
                    'role': 'system',
                    'content': system_prompt,
                },
                {
                    'role': 'user',
                    'content': f'Translate from {source_lang} to {target_lang}:\n{text}',
                },
            ],
        )
        translated = resp.choices[0].message.content.strip()
        return JsonResponse({'translated': translated})
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f'Caption translation failed: {e}')
        return JsonResponse({'error': 'Translation failed'}, status=502)


# ── Document Views ───────────────────────────────────────────────────

@login_required
@require_POST
def document_upload_source(request, pk):
    """Upload files to be translated."""
    order = get_object_or_404(WorkOrder, pk=pk)
    if not _can_supply_and_review_documents(request.user, _get_profile(request.user), order):
        return HttpResponseForbidden('غير مسموح')

    fallback = _documents_url(order)
    form = DocumentUploadForm(request.POST, request.FILES, order=order)
    if not form.is_valid():
        _flash_form_errors(request, form)
        return _redirect_back(request, fallback)

    with transaction.atomic():
        documents = _save_documents(
            order, form.cleaned_data['files'], OrderDocument.Kind.SOURCE,
            request.user, note=form.cleaned_data['note'],
        )
    notify_source_documents_uploaded(order, documents, request.user)
    messages.success(request, f'تم رفع {len(documents)} ملف(ات)')
    return _redirect_back(request, fallback)


@login_required
@require_POST
def document_upload_translation(request, pk, ll_pk):
    """Upload the translation for a language line (a revision addresses open notes)."""
    order = get_object_or_404(WorkOrder, pk=pk)
    language_line = get_object_or_404(WorkOrderLanguage, pk=ll_pk, work_order=order)
    if not _can_upload_translation(request.user, _get_profile(request.user), order, language_line):
        return HttpResponseForbidden('غير مسموح')

    fallback = _documents_url(order, f'line-{language_line.pk}')
    form = DocumentUploadForm(request.POST, request.FILES, order=order)
    if not form.is_valid():
        _flash_form_errors(request, form)
        return _redirect_back(request, fallback)

    assignment = language_line.assignments.filter(translator=request.user).exclude(
        status=OrderAssignment.Status.DECLINED
    ).first()
    with transaction.atomic():
        documents, addressed = _save_translation(
            order, language_line, form.cleaned_data['files'], request.user,
            assignment=assignment, note=form.cleaned_data['note'],
        )
    notify_translation_uploaded(
        order, language_line, documents, request.user, addressed_count=addressed,
    )
    messages.success(request, f'تم رفع الترجمة ({len(documents)} ملف)')
    if addressed:
        messages.info(request, f'تمت معالجة {addressed} ملاحظة مراجعة')
    return _redirect_back(request, fallback)


def _document_response(request, pk, doc_pk, *, inline):
    order = get_object_or_404(WorkOrder, pk=pk)
    if not _can_access_documents(request.user, _get_profile(request.user), order):
        return HttpResponseForbidden('غير مسموح')
    document = get_object_or_404(
        OrderDocument, pk=doc_pk, work_order=order, purged_at__isnull=True,
    )
    if inline and not document.is_previewable:
        raise Http404('لا تتوفر معاينة لهذا النوع من الملفات')

    storage, name = document.file.storage, document.file.name
    disposition = content_disposition_header(not inline, document.original_name)
    if _uses_presigned_urls(storage):
        response = HttpResponseRedirect(storage.url(name, parameters={
            'ResponseContentDisposition': disposition,
            'ResponseContentType': document.content_type,
        }, expire=60))
        response['X-Content-Type-Options'] = 'nosniff'
        return response
    return _private_file_response(
        storage.path(name), f'/internal-documents/{name}', document.content_type, disposition,
    )


@login_required
def document_download(request, pk, doc_pk):
    return _document_response(request, pk, doc_pk, inline=False)


@login_required
def document_preview(request, pk, doc_pk):
    """Open a PDF or image in the browser."""
    return _document_response(request, pk, doc_pk, inline=True)


@login_required
@require_POST
def document_delete(request, pk, doc_pk):
    order = get_object_or_404(WorkOrder, pk=pk)
    document = get_object_or_404(OrderDocument, pk=doc_pk, work_order=order)
    if not _can_delete_document(request.user, order, document):
        return HttpResponseForbidden('غير مسموح')

    anchor = f'line-{document.language_line_id}' if document.language_line_id else 'documents'
    storage, name = document.file.storage, document.file.name
    document.delete()
    storage.delete(name)
    messages.success(request, 'تم حذف الملف')
    return _redirect_back(request, _documents_url(order, anchor))


@login_required
@require_POST
def document_note_add(request, pk, ll_pk):
    """Raise a revision note on a language line's translation."""
    order = get_object_or_404(WorkOrder, pk=pk)
    language_line = get_object_or_404(WorkOrderLanguage, pk=ll_pk, work_order=order)
    if not _can_supply_and_review_documents(request.user, _get_profile(request.user), order):
        return HttpResponseForbidden('غير مسموح')

    fallback = _documents_url(order, f'line-{language_line.pk}')
    form = DocumentNoteForm(request.POST, language_line=language_line)
    if not form.is_valid():
        _flash_form_errors(request, form)
        return _redirect_back(request, fallback)

    note = DocumentNote.objects.create(
        work_order=order, language_line=language_line,
        document=form.cleaned_data['document'], author=request.user,
        body=form.cleaned_data['body'],
    )
    notify_revision_requested(note)
    messages.success(request, 'تم إرسال الملاحظة إلى المترجم')
    return _redirect_back(request, fallback)
