"""Glossary guidance added to the AI caption-translation prompt."""
from django.conf import settings

from .matching import find_terms_in_text

MAX_PROMPT_TERMS = 30


def caption_language(value):
    """'ar' / 'en' for what the conference page sends (a display name or a code)."""
    value = (value or '').strip()
    code = value.lower().split('-')[0]
    if value == 'العربية' or code in ('ar', 'arabic'):
        return 'ar'
    if code in ('en', 'english'):
        return 'en'
    return None


def glossary_prompt(text, source_lang, target_lang):
    """Mandatory translations for approved terms that occur in ``text``, or ''."""
    if not settings.GLOSSARY_IN_CAPTIONS:
        return ''
    source, target = caption_language(source_lang), caption_language(target_lang)
    if {source, target} != {'ar', 'en'}:
        return ''
    terms = find_terms_in_text(text, source, limit=MAX_PROMPT_TERMS)
    if not terms:
        return ''

    lines = ['', '', 'GLOSSARY — use these exact translations for terms that appear in the text:']
    for term in terms:
        original, translation = (
            (term.term_ar, term.term_en) if source == 'ar' else (term.term_en, term.term_ar)
        )
        line = f'- "{original}" → "{translation}"'
        forbidden = term.forbidden_variants(target)
        if forbidden:
            line += ' (do not use: ' + ', '.join(f'"{variant}"' for variant in forbidden) + ')'
        lines.append(line)
    return '\n'.join(lines)
