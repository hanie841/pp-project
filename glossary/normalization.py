"""Text normalization for the Arabic–English glossary.

Both search and in-text matching compare normalized forms, so a term is found
however it was typed: with or without tashkeel or tatweel, any hamza/alef
variant, ى/ي, ة/ه, and any English capitalization or punctuation.
"""
import re
import unicodedata

_ARABIC_LETTER = re.compile(r'[ء-ي]')
# Tashkeel (fathatan … sukun), superscript alef, tatweel.
_ARABIC_MARKS = re.compile(r'[ً-ْٰـ]')
_ARABIC_FOLD = str.maketrans({
    'أ': 'ا', 'إ': 'ا', 'آ': 'ا', 'ٱ': 'ا',
    'ى': 'ي', 'ة': 'ه', 'ؤ': 'و', 'ئ': 'ي',
})
_NON_WORD = re.compile(r'[^\w\s]')
_SPACES = re.compile(r'\s+')

# Letters attached to the front of an Arabic word: و/ف/ب/ل/ك, the article ال,
# or both ("بالعقد"), and لل ("للمحكمة").
_CLITIC_PREFIX = re.compile(r'^(?:[وفبلك]?ال|لل|[وفبلك])')
MIN_STEM_LENGTH = 2


def contains_arabic(text):
    return bool(_ARABIC_LETTER.search(text or ''))


def normalize(text):
    """Canonical form used for storage, search and matching (Arabic and English)."""
    text = unicodedata.normalize('NFKC', text or '')
    text = _ARABIC_MARKS.sub('', text)
    text = text.translate(_ARABIC_FOLD).casefold()
    text = _NON_WORD.sub(' ', text)
    return _SPACES.sub(' ', text).strip()


def tokens(text):
    normalized = normalize(text)
    return normalized.split(' ') if normalized else []


def candidates(token):
    """Forms a normalized token can match: itself, plus without Arabic clitics."""
    forms = [token]
    if contains_arabic(token):
        stem = _CLITIC_PREFIX.sub('', token, count=1)
        if stem != token and len(stem) >= MIN_STEM_LENGTH:
            forms.append(stem)
    return forms


def sequence_matches(text_tokens, start, term_tokens):
    """True if ``term_tokens`` occur in ``text_tokens`` starting at ``start``."""
    if start + len(term_tokens) > len(text_tokens):
        return False
    return all(
        set(candidates(text_tokens[start + offset])) & set(candidates(term_token))
        for offset, term_token in enumerate(term_tokens)
    )
