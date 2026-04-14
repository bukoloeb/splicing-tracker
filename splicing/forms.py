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


# --- Helper Functions (Updated for Database Safety) ---
def get_field_engineers():
    try:
        fe_group = Group.objects.filter(name='Field_Engineers').first()
        if fe_group:
            return fe_group.user_set.all().order_by('username')
    except Exception:
        pass
    return User.objects.none()


def get_contractor_users():
    try:
        contractor_group = Group.objects.filter(name='Contractors').first()
        if contractor_group:
            return contractor_group.user_set.all().order_by('username')
    except Exception:
        pass
    return User.objects.none()


def get_service_delivery_users():
    try:
        sd_group = Group.objects.filter(name='Service_Delivery').first()
        if sd_group:
            return sd_group.user_set.all().order_by('username')
    except Exception:
        pass
    return User.objects.none()


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
            'street_address': forms.TextInput(attrs={'placeholder': 'Block number, street name'}),
            'description': forms.Textarea(attrs={'rows': 5}),
            'comment': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.widget.attrs.update({'class': 'w-full'})

        # Chained Dropdown
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
    assigned_fe = forms.ModelChoiceField(queryset=User.objects.none(), required=False)
    required_completion_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), required=False)
    splicing_contractor_company = forms.ChoiceField(required=False)

    class Meta:
        model = SplicingJob
        fields = ['assigned_fe', 'splicing_contractor_company', 'required_completion_date', 'comment']

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        self.fields['assigned_fe'].queryset = get_field_engineers()

        # Contractor Logic
        names = UserProfile.objects.filter(user__groups__name='Contractors').exclude(
            Q(contractor_company_name__isnull=True) | Q(contractor_company_name='')
        ).values_list('contractor_company_name', flat=True).distinct()

        self.fields['splicing_contractor_company'].choices = [('', '--- Select Contractor ---')] + [(n, n) for n in
                                                                                                    names]
        for name, field in self.fields.items():
            field.widget.attrs.update({'class': 'w-full'})


# =======================================================================
# --- 3. FE STATUS UPDATE FORM ---
# =======================================================================
class FEStatusUpdateForm(forms.ModelForm):
    pop_location_fk = forms.ModelChoiceField(queryset=PopLocation.objects.all(), required=False)
    switch_fk = forms.ModelChoiceField(queryset=Switch.objects.none(), required=False)
    trace_attachment = forms.FileField(required=False)
    splicing_picture = forms.FileField(required=False)

    class Meta:
        model = SplicingJob
        fields = ['pop_location_fk', 'switch_fk', 'port_number', 'status', 'comment', 'trace_attachment',
                  'splicing_picture']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['status'].choices = [
            (SplicingJob.JOB_IN_PROGRESS, 'Splicing In Progress'),
            (SplicingJob.SERVICE_DELIVERY_PENDING, 'Splicing Completed'),
        ]

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

        for name, field in self.fields.items():
            field.widget.attrs.update({'class': 'w-full'})


# =======================================================================
# --- 4. JOB METADATA UPDATE FORM ---
# =======================================================================
class JobMetadataUpdateForm(forms.ModelForm):
    pop_location_fk = forms.ModelChoiceField(queryset=PopLocation.objects.all(), required=False)
    switch_fk = forms.ModelChoiceField(queryset=Switch.objects.none(), required=False)

    class Meta:
        model = SplicingJob
        fields = [
            'job_id', 'customer_name', 'project_code', 'required_completion_date', 'priority',
            'street_address', 'neighbourhood', 'city', 'pop_location_fk', 'switch_fk', 'port_number'
        ]
        widgets = {'required_completion_date': forms.DateInput(attrs={'type': 'date'}),
                   'job_id': forms.TextInput(attrs={'readonly': 'readonly'})}

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

        pop_id = self.instance.pop_location_fk.pk if self.instance.pk and self.instance.pop_location_fk else None
        if pop_id:
            self.fields['switch_fk'].queryset = Switch.objects.filter(pop_location_id=pop_id)

        for name, field in self.fields.items():
            field.widget.attrs.update({'class': 'w-full'})


# =======================================================================
# --- 4.1. JOB CLOSEOUT FORM ---
# =======================================================================
class JobCloseoutForm(forms.ModelForm):
    class Meta:
        model = SplicingJob
        fields = ['closeout_comment']
        widgets = {'closeout_comment': forms.Textarea(attrs={'rows': 5, 'class': 'w-full'})}


# =======================================================================
# --- 6. DASHBOARD FILTER/SEARCH FORM ---
# =======================================================================
class JobFilterForm(forms.Form):
    search_query = forms.CharField(required=False, widget=forms.TextInput(
        attrs={'class': 'w-full rounded-md border-2 border-blue-500'}))
    assigned_fe = forms.ModelChoiceField(queryset=User.objects.none(), required=False, empty_label="All Engineers")
    status = forms.ChoiceField(choices=[], required=False)
    priority = forms.ChoiceField(choices=[], required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['assigned_fe'].queryset = get_field_engineers()
        self.fields['status'].choices = [('', 'All Statuses')] + SplicingJob.STATUS_CHOICES
        self.fields['priority'].choices = [('', 'All Priorities')] + SplicingJob.PRIORITY_CHOICES


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
        if self.instance.pk and self.instance.pop_location_fk:
            self.fields['switch_fk'].queryset = Switch.objects.filter(pop_location_id=self.instance.pop_location_fk.pk)
            self.fields['pop_location_fk'].widget.attrs['disabled'] = 'disabled'
            self.fields['switch_fk'].widget.attrs['disabled'] = 'disabled'
        for name, field in self.fields.items():
            field.widget.attrs.update({'class': 'w-full'})

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('status') == 'COMPLETED':
            cleaned_data['status'] = SplicingJob.SERVICE_DELIVERY_PENDING
        return cleaned_data


# =======================================================================
# --- 8. SERVICE PROVISIONING FORM ---
# =======================================================================
class ProvisioningRecordForm(forms.ModelForm):
    class Meta:
        model = ProvisioningRecord
        fields = ['vlan_id', 'ip_address', 'subnet_mask', 'gateway_ip', 'service_type', 'capacity_mbps',
                  'final_service_notes']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.widget.attrs.update({'class': 'w-full'})