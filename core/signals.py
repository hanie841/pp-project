from django.core.mail import send_mail
from django.conf import settings
from .models import UserProfile
import logging

logger = logging.getLogger(__name__)


def _get_emails_by_role(role):
    return list(
        UserProfile.objects.filter(role=role)
        .exclude(user__email='')
        .values_list('user__email', flat=True)
    )


def _send(subject, message, recipients):
    if not recipients:
        return
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipients,
            fail_silently=True,
        )
    except Exception as e:
        logger.error(f'Email send failed: {e}')


def notify_new_order(order):
    # Send to both SmartWorld admins and contract managers
    recipients = _get_emails_by_role(UserProfile.Role.SMARTWORLD_ADMIN)
    recipients.extend(_get_emails_by_role(UserProfile.Role.CONTRACT_MANAGER))
    _send(
        subject=f'أمر تكليف جديد: {order.order_number}',
        message=(
            f'تم تقديم أمر تكليف جديد رقم {order.order_number}\n'
            f'النيابة: {order.prosecution.name}\n'
            f'نوع الخدمة: {order.get_service_type_display()}\n'
            f'تاريخ التنفيذ: {order.execution_date}\n'
        ),
        recipients=list(set(recipients)),
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
    _send(
        subject=f'رابط الاجتماع: {order.order_number}',
        message=(
            f'تم توفير رابط الاجتماع لأمر التكليف رقم {order.order_number}\n'
            f'الرابط: {order.location_detail}'
        ),
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
    recipients = _get_emails_by_role(UserProfile.Role.SMARTWORLD_ADMIN)
    recipients.extend(_get_emails_by_role(UserProfile.Role.CONTRACT_MANAGER))
    _send(
        subject=f'تم اعتماد أمر التكليف: {order.order_number}',
        message=(
            f'تم اعتماد أمر التكليف رقم {order.order_number} من قبل النيابة.\n'
            f'شهادة الإنجاز جاهزة.'
        ),
        recipients=list(set(recipients)),
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
