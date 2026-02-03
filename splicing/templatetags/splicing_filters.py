from django import template
from django.template.defaultfilters import stringfilter
from datetime import timedelta
from django.contrib.auth.models import Group
from django.db.models import QuerySet
# CRITICAL IMPORT: Need access to your model to fetch STATUS_CHOICES
from splicing.models import SplicingJob

# Register a template library instance
register = template.Library()


# ====================================================================
# --- 1. CORE MATH / UTILITY FILTERS ---
# ====================================================================

@register.filter
def floordiv(value, arg):
    """Performs floor division (integer division). Example: {{ total_seconds|floordiv:86400 }}"""
    try:
        # Ensure division is only attempted if arg is non-zero
        if arg == 0:
            return 0
        return value // arg
    except (ValueError, TypeError, ZeroDivisionError):
        return 0


@register.filter
def modulo(value, arg):
    """Performs modulo operation (remainder). Example: {{ total_seconds|modulo:86400 }}"""
    try:
        # Ensure modulo is only attempted if arg is non-zero
        if arg == 0:
            return 0
        return value % arg
    except (ValueError, TypeError, ZeroDivisionError):
        return 0


@register.filter
def get_item(dictionary, key):
    """
    Retrieves an item from a dictionary-like object using a key.
    Example: {{ splice_performance_formsets|get_item:manhole_form.instance.pk }}
    """
    # Safely try to get the item, returning None if the key doesn't exist
    if dictionary is None:
        return None
    return dictionary.get(key)


@register.filter
def get_prefix(formset):
    """
    Retrieves the 'prefix' attribute from a Django Formset object.
    """
    if hasattr(formset, 'prefix'):
        return formset.prefix
    return 'default_prefix'


@register.filter(name='replace')
@stringfilter
def replace_substring(value, arg):
    """
    Replaces all occurrences of a substring with another string.
    Example: {{ field|replace:'-0-:-__prefix__-' }}
    """
    if ':' not in arg:
        return value

    try:
        # Split the argument by the colon
        old, new = arg.split(':', 1)
        return value.replace(old, new)
    except Exception:
        return value


@register.filter(name='in_group')
def in_group(user, group_name):
    """
    Checks if the user belongs to the specified group.
    Usage: {{ user|in_group:"Group_Name" }}
    """
    if user.is_authenticated:
        # Check if the user is in the group by name
        return user.groups.filter(name=group_name).exists()
    return False


@register.filter(name='is_service_delivery')
def is_service_delivery(user):
    """
    Checks if the user belongs to the Service_Delivery group.
    CRITICAL FILTER for Service Provisioning link visibility.
    """
    if user.is_authenticated:
        # NOTE: Assumes your Service Delivery group is named 'Service_Delivery'
        return user.groups.filter(name='Service_Delivery').exists()
    return False


# ====================================================================
# --- 2. DURATION / TIME FILTERS ---
# ====================================================================

@register.filter
def format_duration(duration):
    """
    Formats a datetime.timedelta object (or DurationField) into a readable string
    (e.g., '1 day, 5 hours'). (Detailed format)
    """
    if duration is None or not isinstance(duration, timedelta):
        return 'N/A'

    total_seconds = int(duration.total_seconds())

    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    parts = []
    if days > 0:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours > 0:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    # Only show minutes if less than a day OR if it's the second largest unit
    if minutes > 0 and (days == 0 or len(parts) < 2):
        parts.append(f"{minutes} min{'s' if minutes != 1 else ''}")

    if not parts and seconds > 0:
        parts.append(f"{seconds} sec{'s' if seconds != 1 else ''}")

    if not parts:
        return '0 minutes'

    # Show a maximum of two time units for conciseness
    return ', '.join(parts[:2])


@register.filter
def metric_duration_format(td):
    """
    Converts a timedelta object into a concise, human-readable string for KPIs
    (e.g., '5d 10h 30m'). (Concise format for Reports)
    """
    if not isinstance(td, timedelta):
        return td

    total_seconds = int(td.total_seconds())
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60

    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    # Only show minutes if there are no days/hours OR if it's the next unit down
    if minutes > 0 and len(parts) < 2:
        parts.append(f"{minutes}m")

    if not parts:
        # Fallback to show seconds if duration is very short
        seconds = total_seconds % 60
        if seconds > 0:
            return f"{seconds}s"
        return "0m"

    return " ".join(parts)


# ====================================================================
# --- 3. STATUS / WORKLOAD FILTERS ---
# ====================================================================

@register.filter
@stringfilter
def status_class_map(value):
    """Maps job status string to a Tailwind CSS class for visual coloring."""
    status = value.upper()

    # Yellow/Orange for Pending Service Delivery Action
    if 'SERVICE_DELIVERY_PENDING' in status or 'PENDING' in status:
        return 'bg-yellow-100 text-yellow-800'

        # Green for Success/Completion
    elif 'PROVISIONED' in status or 'CLOSED' in status or 'COMPLETED' in status:
        return 'bg-green-100 text-green-800'

    # Blue for Active/Assigned
    elif 'PROGRESS' in status or 'ASSIGNED' in status:
        return 'bg-blue-100 text-blue-800'

    # Default/Unknown
    return 'bg-gray-100 text-gray-800'


@register.filter
@stringfilter
def priority_color(value):
    """Maps job priority level to a Tailwind/Bootstrap CSS class for color."""
    priority = value.upper()
    if 'HIGH' in priority:
        # Red for urgent/high priority
        return 'bg-red-100 text-red-800'
    elif 'MEDIUM' in priority:
        # Yellow/Orange for medium priority
        return 'bg-yellow-100 text-yellow-800'
    elif 'LOW' in priority:
        # Green/Blue for low priority
        return 'bg-green-100 text-green-800'
    # Default/Unknown
    return 'bg-gray-100 text-gray-800'


@register.filter
def status_display(status_code):
    """
    Converts a status code key into its human-readable display value.
    (Crucial for Technical Manager report status counts)
    """
    if not status_code:
        return "N/A"

    # Get the choices dictionary from the SplicingJob model
    choices = dict(SplicingJob.STATUS_CHOICES)

    # Return the display value or a title-cased fallback if not found
    return choices.get(status_code, status_code.replace('_', ' ').title())


@register.filter
def map_active_count(queryset):
    """
    Takes a queryset (like fe_active_workload) and returns a list of
    just the 'active_count' values (integers).
    Used to find the max load for percentage calculation.
    """
    if isinstance(queryset, QuerySet) or isinstance(queryset, list):
        # Extract the 'active_count' values from the dictionary objects
        return [item.get('active_count', 0) for item in queryset]
    return []


@register.filter
def get_percentage(value, max_value):
    """
    Calculates the percentage of a value relative to a maximum value.
    Returns the percentage as a string (e.g., '50').
    Usage: {{ fe.active_count|get_percentage:max_load }}
    """
    try:
        value = float(value)
        max_value = float(max_value)
        if max_value == 0:
            return 0
        # Return percentage rounded to the nearest whole number
        return f"{(value / max_value) * 100:.0f}"
    except (ValueError, TypeError, ZeroDivisionError):
        return 0