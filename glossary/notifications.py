"""Glossary e-mails (links only), sent through the portal's notification helper."""
from django.conf import settings
from django.urls import reverse

from core.models import UserProfile
from core.signals import _get_emails_by_role, _send


def _term_url(term):
    return settings.SITE_URL + reverse('glossary:term_detail', args=[term.pk])


def _name(user):
    return user.get_full_name() or user.username


def notify_term_proposed(term):
    """A new proposal waits for review — tell the glossary managers."""
    recipients = _get_emails_by_role(UserProfile.Role.SMARTWORLD_ADMIN)
    recipients += _get_emails_by_role(UserProfile.Role.CONTRACT_MANAGER)
    proposer = term.proposed_by
    _send(
        subject=f'مصطلح مقترح للمراجعة: {term.term_ar} — {term.term_en}',
        message=(
            f'اقترح {_name(proposer)} إضافة مصطلح إلى المسرد القانوني:\n'
            f'{term.term_ar} — {term.term_en}\n\n'
            f'للمراجعة (يلزم تسجيل الدخول):\n{_term_url(term)}'
        ),
        recipients=[email for email in set(recipients) if email and email != proposer.email],
    )


def notify_term_reviewed(term):
    """Tell the proposer whether their term was approved or rejected."""
    proposer = term.proposed_by
    if not proposer or not proposer.email or proposer == term.reviewed_by:
        return
    approved = term.status == term.Status.APPROVED
    outcome = 'تم اعتماد' if approved else 'تم رفض'
    note = f'\nملاحظة المراجع: {term.review_note}' if term.review_note else ''
    _send(
        subject=f'{outcome} المصطلح المقترح: {term.term_ar} — {term.term_en}',
        message=(
            f'{outcome} المصطلح الذي اقترحته في المسرد القانوني:\n'
            f'{term.term_ar} — {term.term_en}{note}\n\n'
            f'{_term_url(term)}'
        ),
        recipients=[proposer.email],
    )
