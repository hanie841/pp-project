from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login, logout, authenticate
from django.contrib import messages
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.utils import timezone
from django.conf import settings
from django.db.models import Sum, Q
from decimal import Decimal

from .models import (
    WorkOrder, WorkOrderLanguage, ServiceRecord,
    WorkOrderApproval, CompletionCertificate,
    Prosecution, Prosecutor, Language, UserProfile,
    TranslatorProfile, OrderAssignment, WorkflowConfig,
    ConferenceRecording,
)
from .forms import (
    WorkOrderForm, WorkOrderLanguageFormSet,
    ServiceRecordForm, MeetingLinkForm, DisputeForm,
    AssignTranslatorForm, TranslatorServiceForm,
)
from .signals import (
    notify_new_order, notify_order_accepted, notify_meeting_link,
    notify_actuals_logged, notify_pp_approved, notify_pp_disputed,
    notify_translator_assigned, notify_assignment_accepted,
    notify_assignment_declined, notify_all_assignments_completed,
)
from .livekit_utils import (
    create_room, generate_join_token, update_room_metadata,
    start_room_recording, stop_recording, list_egress,
)


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            next_url = request.GET.get('next', '/')
            return redirect(next_url)
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
        context['pending_assignments'] = OrderAssignment.objects.filter(
            translator=request.user,
            status=OrderAssignment.Status.PENDING,
        ).select_related('work_order', 'language_line', 'language_line__language')[:10]
        context['active_assignments'] = OrderAssignment.objects.filter(
            translator=request.user,
            status=OrderAssignment.Status.ACCEPTED,
        ).select_related('work_order', 'language_line', 'language_line__language')[:10]
        context['completed_assignments'] = OrderAssignment.objects.filter(
            translator=request.user,
            status=OrderAssignment.Status.COMPLETED,
        ).select_related('work_order', 'language_line', 'language_line__language')[:10]
        config = WorkflowConfig.get_config()
        context['workflow_mode'] = config.mode

    recent_statuses = [
        WorkOrder.Status.SUBMITTED, WorkOrder.Status.ACCEPTED,
        WorkOrder.Status.ASSIGNED, WorkOrder.Status.PENDING_APPROVAL,
        WorkOrder.Status.COMPLETED,
    ]
    context['recent_activity'] = WorkOrder.objects.filter(
        status__in=recent_statuses
    ).order_by('-updated_at')[:10]

    return render(request, 'dashboard.html', context)


@login_required
def order_list(request):
    profile = _get_profile(request.user)
    orders = WorkOrder.objects.all()
    if profile and profile.is_pp_staff:
        orders = orders.filter(
            Q(created_by=request.user) | Q(prosecution=profile.prosecution)
        )

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
        form = WorkOrderForm(request.POST, user=request.user)
        formset = WorkOrderLanguageFormSet(request.POST)
        if form.is_valid() and formset.is_valid():
            order = form.save(commit=False)
            order.created_by = request.user
            order.status = WorkOrder.Status.SUBMITTED
            order.submitted_at = timezone.now()
            order.save()
            formset.instance = order
            formset.save()
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
    })


@login_required
def order_detail(request, pk):
    order = get_object_or_404(WorkOrder, pk=pk)
    profile = _get_profile(request.user)

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
    })


@login_required
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
        all_valid = True
        records = []
        for lang_line in lang_lines:
            prefix = f'lang_{lang_line.pk}'
            actual_hours = request.POST.get(f'{prefix}_hours')
            actual_pages = request.POST.get(f'{prefix}_pages')
            num_translators = request.POST.get(f'{prefix}_translators', lang_line.num_translators)
            notes = request.POST.get(f'{prefix}_notes', '')

            record = ServiceRecord(
                work_order=order,
                language=lang_line.language,
                num_translators=int(num_translators),
                notes=notes,
            )
            if actual_hours:
                record.actual_hours = Decimal(actual_hours)
            if actual_pages:
                record.actual_pages = Decimal(actual_pages)
            record.calculate_amount()
            records.append(record)

        # Delete old records and save new ones
        order.service_records.all().delete()
        for record in records:
            record.save()

        # Create or update approval
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
    if not request.user.is_superuser and (not profile or not profile.is_pp_staff):
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
    })


@login_required
def order_certificate(request, pk):
    order = get_object_or_404(WorkOrder, pk=pk)
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
        'profile': _get_profile(request.user),
    })


@login_required
def order_pdf(request, pk):
    order = get_object_or_404(WorkOrder, pk=pk)
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

    pending = OrderAssignment.objects.filter(
        translator=request.user,
        status=OrderAssignment.Status.PENDING,
    ).select_related('work_order', 'language_line', 'language_line__language')

    accepted = OrderAssignment.objects.filter(
        translator=request.user,
        status=OrderAssignment.Status.ACCEPTED,
    ).select_related('work_order', 'language_line', 'language_line__language')

    completed = OrderAssignment.objects.filter(
        translator=request.user,
        status=OrderAssignment.Status.COMPLETED,
    ).select_related('work_order', 'language_line', 'language_line__language')[:20]

    config = WorkflowConfig.get_config()

    return render(request, 'orders/translator_dashboard.html', {
        'pending_assignments': pending,
        'active_assignments': accepted,
        'completed_assignments': completed,
        'workflow_mode': config.mode,
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
    form = TranslatorServiceForm(request.POST or None)

    if request.method == 'POST' and form.is_valid():
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

        # Link record to assignment and mark completed
        assignment.service_record = record
        assignment.status = OrderAssignment.Status.COMPLETED
        assignment.completed_at = timezone.now()
        assignment.save()

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

    options = '<option value="">---------</option>'
    for p in prosecutors:
        options += f'<option value="{p.pk}">{p.name}</option>'
    return HttpResponse(options)


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

    full_path = os.path.join(settings.RECORDING_ROOT, recording.file_path)
    filename = os.path.basename(recording.file_path)

    # Use nginx X-Accel-Redirect in production
    if not settings.DEBUG:
        response = HttpResponse()
        response['X-Accel-Redirect'] = f'/internal-recordings/{recording.file_path}'
        response['Content-Type'] = 'video/mp4'
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response

    # Dev fallback: serve via Django
    from django.http import FileResponse
    if not os.path.exists(full_path):
        return HttpResponse('الملف غير موجود', status=404)
    return FileResponse(
        open(full_path, 'rb'),
        content_type='video/mp4',
        as_attachment=True,
        filename=filename,
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

    try:
        resp = client.chat.completions.create(
            model='gpt-4o-mini',
            temperature=0.1,
            max_tokens=500,
            messages=[
                {
                    'role': 'system',
                    'content': (
                        'You are a legal translation assistant for UAE Federal Prosecution. '
                        'Translate the following text accurately and concisely. '
                        'Preserve legal terminology. Return ONLY the translated text, '
                        'no explanations or notes.'
                    ),
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
