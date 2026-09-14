"""Who can see, propose, edit, review and link glossary terms."""
from core.models import OrderAssignment, WorkOrder
from core.views import _can_supply_and_review_documents, _get_profile


def can_view(user):
    return user.is_authenticated and (user.is_superuser or _get_profile(user) is not None)


def is_manager(user):
    """SmartWorld admins and the contract manager maintain the glossary."""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    profile = _get_profile(user)
    return bool(profile and (profile.is_smartworld_admin or profile.is_contract_manager))


def can_edit(user, term):
    if is_manager(user):
        return True
    return (
        term.status == term.Status.PROPOSED
        and not term.is_archived
        and term.proposed_by_id == user.pk
    )


def can_link_terms(user, order):
    """Same people who supply and review the order's documents, plus its translators."""
    if order.status == WorkOrder.Status.COMPLETED:
        return False
    profile = _get_profile(user)
    if _can_supply_and_review_documents(user, profile, order):
        return True
    return bool(profile and profile.is_translator) and order.assignments.filter(
        translator=user
    ).exclude(status=OrderAssignment.Status.DECLINED).exists()
