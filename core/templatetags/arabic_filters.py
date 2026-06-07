from django import template
from decimal import Decimal
import json

register = template.Library()

ARABIC_DIGITS = '٠١٢٣٤٥٦٧٨٩'


@register.filter
def to_arabic_digits(value):
    """Convert Western digits to Arabic digits"""
    s = str(value)
    return ''.join(ARABIC_DIGITS[int(c)] if c.isdigit() else c for c in s)


@register.filter
def currency(value):
    """Format a number as AED currency"""
    try:
        val = Decimal(str(value))
        formatted = f'{val:,.2f}'
        return f'{formatted} درهم'
    except (ValueError, TypeError):
        return value


@register.filter
def to_json(value):
    """Convert a Python dict to JSON for use in templates"""
    return json.dumps(value)
