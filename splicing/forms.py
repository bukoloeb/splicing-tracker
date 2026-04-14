import os
from django import forms
from django.contrib.auth.models import User, Group
from django.db.models import Q
from django.core.exceptions import ValidationError
from .models import (
    SplicingJob,
    PopLocation,
    Switch,
    UserProfile,
    ProvisioningRecord
)


# --- Helper Functions (Updated to handle missing tables safely) ---
def get_field_engineers():
    """Returns a queryset of users belonging to the 'Field_Engineers' group."""
    try:
        # Check if auth_group table exists before querying
        fe_group = Group.objects.filter(name='Field_Engineers').first()
        if fe_group:
            return fe_group.user_set.all().order_by('username')
    except Exception:
        pass
    return User.objects.none()


def get_contractor_users():
    """Returns a queryset of users belonging to the 'Contractors' group."""
    try:
        contractor_group = Group.objects.filter(name='Contractors').first()
        if contractor_group:
            return contractor_group.user_set.all().order_by('username')
    except Exception:
        pass
    return User.objects.none()


def get_service_delivery_users():
    """Returns a queryset of users belonging to the 'Service_Delivery' group."""
    try:
        sd_group = Group.objects.filter(name='Service_Delivery').first()
        if sd_group:
            return sd_group.user_set.all().order_by('username')
    except Exception:
        pass
    return User.objects.none()


# --- Contractor Status Choices Definition ---
CONTRACTOR_ALLOWED_STATUSES = (
    (SplicingJob.JOB_IN_PROGRESS, 'Splicing In Progress'),
    (SplicingJob.JOB_ON_HOLD, 'Put Job On Hold'),
    ('COMPLETED', 'Splicing Completed'),
)


# =======================================================================
# --- 1. JOB CREATION FORM ---
# =======================================================================
class SplicingJobCreationForm(forms.ModelForm):
    pop_location_fk = forms.ModelChoiceField(
        queryset=PopLocation.objects.all(),
        required=False,
        label="Target PoP Location",
        empty_label="--- Select PoP ---"
    )
    switch_fk = forms.ModelChoiceField(
        queryset=Switch.objects.none(),
        required=False,
        label="Target Switch",
        empty_label="--- Select Switch ---"
    )
    port_number = forms.CharField(max_length=20, required=False, label="Target Port Number")

    class Meta:
        model = SplicingJob
        fields = [
            'customer_name', 'project_code', 'contact_person', 'contact_number',
            'alt_contact_person', 'alt_contact_number', 'required_completion_date',
            'priority', 'street_address', 'neighbourhood', 'city', 'province',
            'country', 'project_manager', 'civil_contractor_name', 'description',
            'comment', 'pop_location_fk', 'switch_fk', 'port_number',
        ]
        widgets = {
            'required_completion_date': forms.DateInput(attrs={'type': 'date'}),
            'description': forms.Textarea(attrs={'rows': 5}),
            'comment': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.widget.attrs.update({'class': 'w-full'})

        # Chained Dropdown Logic
        pop_id = None
        if 'pop_location_fk' in self.data:
            try:
                pop_id = int(self.data.get('pop_location_fk'))
            except:
                pass
        elif self.instance.pk and self.instance.pop_location_fk:
            pop_id = self.instance.pop_location_fk.pk

        if pop_id:
            self.fields['switch_fk'].queryset = Switch.objects.filter(pop_location_id=pop_id).order_by('name')


# =======================================================================
# --- 2. JOB ASSIGNMENT FORM ---
# =======================================================================
class JobAssignmentForm(forms.ModelForm):
    assigned_fe = forms.ModelChoiceField(
        queryset=User.objects.none(),  # Start empty to prevent crash
        required=False,
        label="Assign to Field Engineer (Internal)",
    )
    splicing_contractor_company = forms.ChoiceField(required=False)

    class Meta:
        model = SplicingJob
        fields = ['assigned_fe', 'splicing_contractor_company', 'required_completion_date', 'comment']

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

        # Populate database-dependent querysets safely in __init__
        self.fields['assigned_fe'].queryset = get_field_engineers()

        contractor_company_names = UserProfile.objects.filter(
            user__groups__name='Contractors'
        ).exclude(contractor_company_name='').values_list('contractor_company_name', flat=True).distinct()

        contractor_choices = [('', '--- Select Contractor ---')] + [(n, n) for n in contractor_company_names]
        self.fields['splicing_contractor_company'].choices = contractor_choices

        for name, field in self.fields.items():
            field.widget.attrs.update({'class': 'w-full'})


# =======================================================================
# --- 6. DASHBOARD FILTER/SEARCH FORM (CRITICAL FIX FOR CRASH) ---
# =======================================================================
class JobFilterForm(forms.Form):
    search_query = forms.CharField(
        required=False,
        label="Search",
        widget=forms.TextInput(attrs={'placeholder': 'Job ID or Project Code', 'class': 'w-full'})
    )

    assigned_fe = forms.ModelChoiceField(
        queryset=User.objects.none(),  # Lazy loaded in __init__
        required=False,
        label="Assigned FE",
        empty_label="All Engineers"
    )

    status = forms.ChoiceField(choices=[], required=False)
    priority = forms.ChoiceField(choices=[], required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Safely load database data when form is called, not when file is loaded
        self.fields['assigned_fe'].queryset = get_field_engineers()
        self.fields['status'].choices = [('', 'All Statuses')] + SplicingJob.STATUS_CHOICES
        self.fields['priority'].choices = [('', 'All Priorities')] + SplicingJob.PRIORITY_CHOICES

        for name, field in self.fields.items():
            field.widget.attrs.update({'class': 'rounded-md border-gray-300'})


# =======================================================================
# --- 7. CONTRACTOR STATUS UPDATE FORM ---
# =======================================================================
class ContractorStatusUpdateForm(forms.ModelForm):
    status = forms.ChoiceField(choices=CONTRACTOR_ALLOWED_STATUSES)
    pop_location_fk = forms.ModelChoiceField(queryset=PopLocation.objects.all(), required=False)
    switch_fk = forms.ModelChoiceField(queryset=Switch.objects.none(), required=False)

    class Meta:
        model = SplicingJob
        fields = ['pop_location_fk', 'switch_fk', 'port_number', 'status', 'comment', 'trace_attachment',
                  'splicing_picture']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = self.instance
        if instance and instance.pop_location_fk:
            pop_id = instance.pop_location_fk.pk
            self.fields['switch_fk'].queryset = Switch.objects.filter(pop_location_id=pop_id)
            # Prevent changing the target Pop/Switch once assigned
            self.fields['pop_location_fk'].widget.attrs['disabled'] = 'disabled'
            self.fields['switch_fk'].widget.attrs['disabled'] = 'disabled'

        for name, field in self.fields.items():
            field.widget.attrs.update({'class': 'w-full'})

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('status') == 'COMPLETED':
            # Map form 'COMPLETED' to model 'SD_PENDING'
            cleaned_data['status'] = SplicingJob.SERVICE_DELIVERY_PENDING

            # Validation for completion
            if not cleaned_data.get('pop_location_fk') or not cleaned_data.get('port_number'):
                raise ValidationError("Network targets (PoP/Port) must be confirmed to complete splicing.")
        return cleaned_data

# (Remaining forms: FEStatusUpdateForm, JobMetadataUpdateForm, ProvisioningRecordForm
# follow the same __init__ pattern as SplicingJobCreationForm above)