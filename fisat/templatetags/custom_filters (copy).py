from django import template

register = template.Library()

@register.filter
def chunkify(value, chunk_size):
    """Splits a list or dictionary items into chunks of a specified size."""
    value = list(value.items())  # Convert dictionary to a list of tuples
    return [value[i:i + chunk_size] for i in range(0, len(value), chunk_size)]

