"""Find approved glossary terms that occur in a piece of text."""
from django.db.models import Count, Max

from .models import GlossaryTerm
from .normalization import candidates, sequence_matches, tokens

# Per-process index of approved terms, keyed by every matchable form of each
# variant's first token. Rebuilt whenever the set of approved terms changes.
_index = {'version': None, 'ar': {}, 'en': {}}


def clear_index_cache():
    _index['version'] = None


def _current_version():
    stats = GlossaryTerm.objects.usable().aggregate(count=Count('pk'), latest=Max('updated_at'))
    return stats['count'], stats['latest']


def _build_index():
    index = {'ar': {}, 'en': {}}
    for term in GlossaryTerm.objects.usable():
        for language in ('ar', 'en'):
            for variant in term.variants(language):
                variant_tokens = tokens(variant)
                if not variant_tokens:
                    continue
                for key in candidates(variant_tokens[0]):
                    index[language].setdefault(key, []).append((variant_tokens, term.pk))
    return index


def find_terms_in_text(text, language, limit=None):
    """Approved terms whose ``language`` form (or a synonym) occurs in ``text``.

    Matching is on whole normalized words, so "contract" does not match
    "subcontractor"; Arabic words may carry attached و/ف/ب/ل/ك and ال.
    Terms are returned in order of first appearance.
    """
    version = _current_version()
    if _index['version'] != version:
        _index.update(_build_index(), version=version)

    language_index = _index[language]
    text_tokens = tokens(text)
    found = []
    for position, token in enumerate(text_tokens):
        for key in candidates(token):
            for variant_tokens, term_pk in language_index.get(key, ()):
                if term_pk not in found and sequence_matches(text_tokens, position, variant_tokens):
                    found.append(term_pk)

    if limit:
        found = found[:limit]
    terms = GlossaryTerm.objects.in_bulk(found)
    return [terms[pk] for pk in found if pk in terms]
