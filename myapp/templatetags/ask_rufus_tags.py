from django import template

from ..ask_rufus_utils import parse_ask_rufus_items

register = template.Library()


@register.filter
def ask_rufus_items(value):
    return parse_ask_rufus_items(value)
