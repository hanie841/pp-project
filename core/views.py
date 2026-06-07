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
)
from .forms import (
    WorkOrderForm, WorkOrderLanguageFormSet,
    ServiceRecordForm, MeetingLinkForm, DisputeForm,
)
from .signals import (
    notify_new_order, notify_order_accepted, notify_meeting_link,
    notify_actuals_logged, notify_pp_approved, notify_pp_disputed,
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
            WorkOrder.Status.SUBMITTED, WorkOrder.Status.ACCEPTED, WorkOrder.Status.IN_PROGRESS,
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
        context['pending_approvals'] = WorkOrder.objects.filter(
            status=WorkOrder.Status.PENDING_APPROVAL
        )[:10]

    recent_statuses = [
        WorkOrder.Status.SUBMITTED, WorkOrder.Status.ACCEPTED,
        WorkOrder.Status.PENDING_APPROVAL, WorkOrder.Status.COMPLETED,
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

    return render(request, 'orders/detail.html', {
        'order': order,
        'profile': profile,
        'meeting_link_form': meeting_link_form,
    })


@login_required
def order_accept(request, pk):
    order = get_object_or_404(WorkOrder, pk=pk)
    profile = _get_profile(request.user)
    if not request.user.is_superuser and (not profile or not profile.is_smartworld):
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
    if order.status not in (WorkOrder.Status.ACCEPTED, WorkOrder.Status.IN_PROGRESS, WorkOrder.Status.DISPUTED):
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
