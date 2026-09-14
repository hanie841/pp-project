"""Glossary search shared by the glossary page and the lookup panel."""
from django.db.models import Case, IntegerField, Q, Value, When

from .normalization import normalize


def search_terms(queryset, query):
    """Filter ``queryset`` by a free-text Arabic or English query and rank it.

    Exact matches on the main term come first, then prefix matches, then any
    term whose normalized text (including synonyms) contains the query.
    """
    needle = normalize(query)
    if not needle:
        return queryset
    return queryset.filter(search_text__contains=needle).annotate(
        rank=Case(
            When(Q(term_ar_normalized=needle) | Q(term_en_normalized=needle), then=Value(0)),
            When(
                Q(term_ar_normalized__startswith=needle) | Q(term_en_normalized__startswith=needle),
                then=Value(1),
            ),
            default=Value(2),
            output_field=IntegerField(),
        )
    ).order_by('rank', 'term_ar_normalized')
