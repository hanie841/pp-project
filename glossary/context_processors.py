from .models import GlossaryTerm
from .permissions import is_manager


def glossary_nav(request):
    """Proposals waiting for review, shown as a badge on the managers' menu link."""
    user = getattr(request, 'user', None)
    if user is None or not is_manager(user):
        return {}
    return {
        'glossary_pending_count': GlossaryTerm.objects.filter(
            status=GlossaryTerm.Status.PROPOSED, is_archived=False,
        ).count(),
    }
