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
    recipients = _get_emails_by_role(UserProfile.Role.SMARTWORLD_ADMIN)
    _send(
        subject=f'أمر تكليف جديد: {order.order_number}',
        message=(
            f'تم تقديم أمر تكليف جديد رقم {order.order_number}\n'
            f'النيابة: {order.prosecution.name}\n'
            f'نوع الخدمة: {order.get_service_type_display()}\n'
            f'تاريخ التنفيذ: {order.execution_date}\n'
        ),
        recipients=recipients,
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
