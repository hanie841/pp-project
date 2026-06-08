from django.core.mail import EmailMessage
from django.conf import settings
from django.template.loader import render_to_string
from .models import UserProfile
import logging
import os
import base64

logger = logging.getLogger(__name__)

COMPANY_EMAIL = 'info@swlt.ae'


def _get_emails_by_role(role):
    return list(
        UserProfile.objects.filter(role=role)
        .exclude(user__email='')
        .values_list('user__email', flat=True)
    )


def _send(subject, message, recipients, attachments=None):
    """Send email with optional attachments.
    attachments: list of (filename, content_bytes, mime_type) tuples
    """
    # Always include company email
    if COMPANY_EMAIL not in recipients:
        recipients.append(COMPANY_EMAIL)
    recipients = list(set(recipients))
    if not recipients:
        return
    try:
        email = EmailMessage(
            subject=subject,
            body=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=recipients,
        )
        if attachments:
            for filename, content, mime_type in attachments:
                email.attach(filename, content, mime_type)
        email.send(fail_silently=True)
    except Exception as e:
        logger.error(f'Email send failed: {e}')


def _generate_order_pdf(order):
    """Generate work order PDF bytes, returns (filename, bytes, mime) or None."""
    try:
        static_dir = settings.STATICFILES_DIRS[0] if settings.STATICFILES_DIRS else settings.STATIC_ROOT
        pp_logo_file = os.path.join(static_dir, 'images', 'pp_logo.png')
        uae_emblem_file = os.path.join(static_dir, 'images', 'uae_emblem.png')
        with open(pp_logo_file, 'rb') as f:
            pp_logo_b64 = base64.b64encode(f.read()).decode()
        with open(uae_emblem_file, 'rb') as f:
            uae_emblem_b64 = base64.b64encode(f.read()).decode()

        lang_lines = order.languages.select_related('language').all()
        context = {
            'order': order,
            'lang_lines': lang_lines,
            'contract_number': settings.CONTRACT_NUMBER,
            'pp_logo_data': f'data:image/png;base64,{pp_logo_b64}',
            'uae_emblem_data': f'data:image/png;base64,{uae_emblem_b64}',
        }
        html = render_to_string('pdf/work_order.html', context)

        try:
            from weasyprint import HTML
            pdf_bytes = HTML(string=html).write_pdf()
            filename = f'work_order_{order.order_number.replace("/", "_")}.pdf'
            return (filename, pdf_bytes, 'application/pdf')
        except ImportError:
            # WeasyPrint not available — attach HTML instead
            filename = f'work_order_{order.order_number.replace("/", "_")}.html'
            return (filename, html.encode('utf-8'), 'text/html')
    except Exception as e:
        logger.error(f'PDF generation failed for order {order.order_number}: {e}')
        return None


def _generate_certificate_pdf(order):
    """Generate completion certificate PDF bytes, returns (filename, bytes, mime) or None."""
    try:
        certificate = order.certificate
    except Exception:
        return None

    try:
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
        html = render_to_string('pdf/certificate.html', context)

        try:
            from weasyprint import HTML
            pdf_bytes = HTML(string=html).write_pdf()
            filename = f'certificate_{certificate.certificate_number.replace("/", "_")}.pdf'
            return (filename, pdf_bytes, 'application/pdf')
        except ImportError:
            filename = f'certificate_{certificate.certificate_number.replace("/", "_")}.html'
            return (filename, html.encode('utf-8'), 'text/html')
    except Exception as e:
        logger.error(f'Certificate PDF generation failed for order {order.order_number}: {e}')
        return None


def notify_new_order(order):
    """New order submitted — notify SmartWorld + CM with work order PDF attached."""
    recipients = _get_emails_by_role(UserProfile.Role.SMARTWORLD_ADMIN)
    recipients.extend(_get_emails_by_role(UserProfile.Role.CONTRACT_MANAGER))

    attachments = []
    pdf = _generate_order_pdf(order)
    if pdf:
        attachments.append(pdf)

    _send(
        subject=f'أمر تكليف جديد: {order.order_number}',
        message=(
            f'تم تقديم أمر تكليف جديد رقم {order.order_number}\n'
            f'النيابة: {order.prosecution.name}\n'
            f'نوع الخدمة: {order.get_service_type_display()}\n'
            f'تاريخ التنفيذ: {order.execution_date}\n'
            f'\nالمرفقات: أمر التكليف'
        ),
        recipients=list(set(recipients)),
        attachments=attachments,
    )


def notify_order_accepted(order):
    recipients = [order.created_by.email] if order.created_by.email else []
    _send(
        subject=f'تم قبول أمر التكليف: {order.order_number}',
        message=f'تم قبول أمر التكليف رقم {order.order_number} من قبل سمارت وورلد.',
        recipients=recipients,
    )


def notify_meeting_link(order):
    recipients = [order.created_by.email] if order.created_by.email else []
    message = (
        f'تم توفير رابط الاجتماع لأمر التكليف رقم {order.order_number}\n'
        f'الرابط: {order.location_detail}'
    )
    if order.conference_room:
        conference_url = f'https://pp.swlt.ae/orders/{order.pk}/conference/'
        message += f'\n\nللانضمام عبر غرفة الاجتماع المرئي:\n{conference_url}'
    _send(
        subject=f'رابط الاجتماع: {order.order_number}',
        message=message,
        recipients=recipients,
    )


def notify_actuals_logged(order):
    recipients = []
    if order.created_by.email:
        recipients.append(order.created_by.email)
    recipients.extend(_get_emails_by_role(UserProfile.Role.CONTRACT_MANAGER))
    _send(
        subject=f'بانتظار اعتمادكم: {order.order_number}',
        message=(
            f'تم تسجيل الخدمة الفعلية لأمر التكليف رقم {order.order_number}\n'
            f'يرجى مراجعة واعتماد الأمر.'
        ),
        recipients=list(set(recipients)),
    )


def notify_pp_approved(order):
    """Order approved — notify with completion certificate attached."""
    recipients = _get_emails_by_role(UserProfile.Role.SMARTWORLD_ADMIN)
    recipients.extend(_get_emails_by_role(UserProfile.Role.CONTRACT_MANAGER))

    attachments = []
    cert_pdf = _generate_certificate_pdf(order)
    if cert_pdf:
        attachments.append(cert_pdf)

    _send(
        subject=f'تم اعتماد أمر التكليف: {order.order_number} - نموذج الإنجاز',
        message=(
            f'تم اعتماد أمر التكليف رقم {order.order_number} من قبل النيابة.\n'
            f'شهادة الإنجاز جاهزة.\n'
            f'\nالمرفقات: نموذج الإنجاز (شهادة إنجاز)'
        ),
        recipients=list(set(recipients)),
        attachments=attachments,
    )


def notify_pp_disputed(order):
    recipients = _get_emails_by_role(UserProfile.Role.SMARTWORLD_ADMIN)
    reason = ''
    if hasattr(order, 'approval') and order.approval.pp_dispute_reason:
        reason = f'\nسبب الاعتراض: {order.approval.pp_dispute_reason}'
    _send(
        subject=f'اعتراض على أمر التكليف: {order.order_number}',
        message=(
            f'تم الاعتراض على أمر التكليف رقم {order.order_number} من قبل النيابة.'
            f'{reason}'
        ),
        recipients=recipients,
    )


def notify_translator_assigned(assignment):
    """Email translator when assigned to an order"""
    if not assignment.translator.email:
        return
    _send(
        subject=f'تعيين جديد: {assignment.work_order.order_number}',
        message=(
            f'تم تعيينك للعمل على أمر التكليف رقم {assignment.work_order.order_number}\n'
            f'اللغة: {assignment.language_line.language_display}\n'
            f'نوع الخدمة: {assignment.work_order.get_service_type_display()}\n'
            f'تاريخ التنفيذ: {assignment.work_order.execution_date}\n'
            f'يرجى تسجيل الدخول لقبول أو رفض التعيين.'
        ),
        recipients=[assignment.translator.email],
    )


def notify_assignment_accepted(assignment):
    """Email CM when translator accepts assignment"""
    recipients = _get_emails_by_role(UserProfile.Role.CONTRACT_MANAGER)
    _send(
        subject=f'قبول تعيين: {assignment.work_order.order_number}',
        message=(
            f'قبل المترجم {assignment.translator.get_full_name()} التعيين '
            f'لأمر التكليف رقم {assignment.work_order.order_number}\n'
            f'اللغة: {assignment.language_line.language_display}'
        ),
        recipients=recipients,
    )


def notify_assignment_declined(assignment):
    """Email CM when translator declines — needs reassignment"""
    recipients = _get_emails_by_role(UserProfile.Role.CONTRACT_MANAGER)
    _send(
        subject=f'رفض تعيين: {assignment.work_order.order_number}',
        message=(
            f'رفض المترجم {assignment.translator.get_full_name()} التعيين '
            f'لأمر التكليف رقم {assignment.work_order.order_number}\n'
            f'اللغة: {assignment.language_line.language_display}\n'
            f'يرجى إعادة تعيين مترجم آخر.'
        ),
        recipients=recipients,
    )


def notify_all_assignments_completed(order):
    """Email PP staff + CM when all translators done"""
    recipients = []
    if order.created_by.email:
        recipients.append(order.created_by.email)
    recipients.extend(_get_emails_by_role(UserProfile.Role.CONTRACT_MANAGER))
    _send(
        subject=f'اكتمال جميع التعيينات: {order.order_number}',
        message=(
            f'أكمل جميع المترجمين عملهم على أمر التكليف رقم {order.order_number}\n'
            f'الأمر بانتظار اعتماد النيابة.'
        ),
        recipients=list(set(recipients)),
    )
