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


# --- Helper Functions ---
def get_field_engineers():
    """Returns a queryset of users belonging to the 'Field_Engineers' group."""
    try:
        fe_group = Group.objects.get(name='Field_Engineers')
        return fe_group.user_set.all().order_by('username')
    except Group.DoesNotExist:
        return User.objects.none()


def get_contractor_users():
    """Returns a queryset of users belonging to the 'Contractors' group."""
    try:
        contractor_group = Group.objects.get(name='Contractors')
        return contractor_group.user_set.all().order_by('username')
    except Group.DoesNotExist:
        return User.objects.none()


def get_service_delivery_users():
    """Returns a queryset of users belonging to the 'Service_Delivery' group."""
    try:
        sd_group = Group.objects.get(name='Service_Delivery')
        return sd_group.user_set.all().order_by('username')
    except Group.DoesNotExist:
        return User.objects.none()


# --- Contractor Status Choices Definition ---
# Note: 'COMPLETED' is a form choice label, not a model constant.
CONTRACTOR_ALLOWED_STATUSES = (
    (SplicingJob.JOB_IN_PROGRESS, 'Splicing In Progress'),
    (SplicingJob.JOB_ON_HOLD, 'Put Job On Hold'),
    ('COMPLETED', 'Splicing Completed'),  # Keep string literal as the form value
)


# ------------------------------------------


# =======================================================================
# --- 1. JOB CREATION FORM (Job Creator Role) ---
# =======================================================================
class SplicingJobCreationForm(forms.ModelForm):
    """
    Job Creator Form: Includes core job fields, NEW contact fields, and NEW network targets.
    """

    # Network Targets (New fields for the creator to set)
    pop_location_fk = forms.ModelChoiceField(
        queryset=PopLocation.objects.all(),
        required=False,
        label="Target PoP Location",
        empty_label="--- Select PoP ---"
    )
    switch_fk = forms.ModelChoiceField(
        queryset=Switch.objects.none(),  # Populated dynamically
        required=False,
        label="Target Switch",
        empty_label="--- Select Switch ---"
    )
    port_number = forms.CharField(
        max_length=20,
        required=False,
        label="Target Port Number"
    )

    class Meta:
        model = SplicingJob
        fields = [
            'customer_name',
            'project_code',

            # NEW CONTACT FIELDS
            'contact_person',
            'contact_number',
            'alt_contact_person',
            'alt_contact_number',

            'required_completion_date',
            'priority',
            'street_address',
            'neighbourhood',
            'city',
            'province',
            'country',
            'project_manager',
            'civil_contractor_name',
            'description',
            'comment',

            # --- CRITICAL FIX START: INCLUDE EXPLICIT FIELDS FOR SAVING ---
            'pop_location_fk',
            'switch_fk',
            'port_number',
            # --- CRITICAL FIX END ---
        ]

        widgets = {
            'required_completion_date': forms.DateInput(attrs={'type': 'date'}),
            'street_address': forms.TextInput(attrs={'placeholder': 'Block number, street name'}),
            'neighbourhood': forms.TextInput(attrs={'placeholder': 'Sub-Area or Zone'}),
            'city': forms.TextInput(attrs={'placeholder': 'City'}),
            'province': forms.TextInput(attrs={'placeholder': 'Province'}),
            'country': forms.TextInput(attrs={'placeholder': 'Zambia'}),
            'description': forms.Textarea(
                attrs={'rows': 5, 'placeholder': 'Detailed scope of works and fiber requirements...'}),
            'comment': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Internal notes...'}),
        }

    def __init__(self, *args, **kwargs):
        # 1. CRITICAL: Capture the current user for permission checks
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

        self.fields['project_manager'].label = 'Project Manager (Oversight)'
        for name, field in self.fields.items():
            if isinstance(field.widget, (forms.TextInput, forms.Textarea, forms.Select, forms.DateInput)):
                field.widget.attrs.update({'class': 'w-full'})

        # --- Required Completion Date Permission Logic ---
        instance = self.instance
        if instance.pk and instance.required_completion_date and instance.creator and self.user:
            is_creator = self.user and (self.user == instance.creator)

            creator_groups = set(instance.creator.groups.values_list('name', flat=True))
            user_groups = set(self.user.groups.values_list('name', flat=True))
            is_group_member = bool(creator_groups.intersection(user_groups))

            # Disable the field if the current user is NOT the creator or in the creator's group
            if not (is_creator or is_group_member):
                self.fields['required_completion_date'].widget.attrs['readonly'] = 'readonly'
                self.fields[
                    'required_completion_date'].help_text = "Only the job creator or their group members can modify this date."

        # --- Setup for Chained Dropdown for PoP/Switch ---
        pop_id = None
        if 'pop_location_fk' in self.data:
            try:
                pop_id = int(self.data.get('pop_location_fk'))
            except (ValueError, TypeError):
                pass
        elif self.instance.pk and self.instance.pop_location_fk:
            pop_id = self.instance.pop_location_fk.pk

        switch_queryset = Switch.objects.none()
        if pop_id:
            switch_queryset = Switch.objects.filter(pop_location_id=pop_id).order_by('name')

        self.fields['switch_fk'].queryset = switch_queryset


# =======================================================================
# --- 2. JOB ASSIGNMENT FORM (Splicing Manager Role) ---
# =======================================================================
class JobAssignmentForm(forms.ModelForm):
    # Field Engineer Assignment
    assigned_fe = forms.ModelChoiceField(
        queryset=User.objects.none(),
        required=False,
        label="Assign to Field Engineer (Internal)",
        empty_label="--- Select an Engineer (Internal) ---",
        widget=forms.Select(attrs={'class': 'w-full'})
    )

    # Required Completion Date (Need to explicitly define here for read-only status in manager view)
    required_completion_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'w-full'}),
        required=False,
        label="Required Completion Date"
    )

    splicing_contractor_company = forms.ChoiceField(
        required=False,
        label="Splicing Contractor Company Name (External)",
        widget=forms.Select(attrs={'class': 'w-full'})
    )

    class Meta:
        model = SplicingJob
        fields = ['assigned_fe', 'splicing_contractor_company', 'required_completion_date', 'comment']

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)  # CRITICAL: Captures the current user
        super().__init__(*args, **kwargs)

        self.fields['assigned_fe'].queryset = get_field_engineers()

        # 1. Query all unique, non-empty contractor company names from UserProfile
        contractor_company_names = []
        if self.user:
            contractor_company_names = UserProfile.objects.filter(
                user__groups__name='Contractors'
            ).exclude(
                Q(contractor_company_name__isnull=True) | Q(contractor_company_name='')
            ).values_list('contractor_company_name', flat=True).distinct()

        # 2. Prepare choices
        contractor_choices = [('', '--------- Select Contractor Company (Optional) ---------')]
        contractor_choices.extend([(name, name) for name in contractor_company_names])

        # 3. Assign the choices
        self.fields['splicing_contractor_company'].choices = contractor_choices

        if self.instance.pk and self.instance.splicing_contractor_company:
            self.fields['splicing_contractor_company'].initial = self.instance.splicing_contractor_company

        self.fields['comment'].widget.attrs.update({'class': 'w-full'})

        # --- Required Completion Date Permission Logic (Copied from Creation Form) ---
        instance = self.instance
        if instance.pk and instance.required_completion_date and instance.creator and self.user:
            is_creator = self.user and (self.user == instance.creator)
            creator_groups = set(instance.creator.groups.values_list('name', flat=True))
            user_groups = set(self.user.groups.values_list('name', flat=True))
            is_group_member = bool(creator_groups.intersection(user_groups))

            # Disable the field if the current user is NOT the creator or in the creator's group
            if not (is_creator or is_group_member):
                self.fields['required_completion_date'].widget.attrs['readonly'] = 'readonly'
                self.fields[
                    'required_completion_date'].help_text = "Only the job creator or their group members can modify this date."

    def clean(self):
        cleaned_data = super().clean()
        assigned_fe = cleaned_data.get('assigned_fe')
        splicing_contractor_company = cleaned_data.get('splicing_contractor_company')

        # Validation Check: Ensure at least one assignment is made (FE or Contractor)
        if not assigned_fe and not splicing_contractor_company:
            self.add_error(None,
                           "You must assign the job to either an Internal Field Engineer or an External Splicing Contractor Company.")

        # Validation Check: Prevent double assignment
        if assigned_fe and splicing_contractor_company:
            self.add_error(None,
                           "A job can only be assigned to *either* an Internal FE *or* an External Contractor, not both.")

        return cleaned_data


# =======================================================================
# --- 3. FE STATUS UPDATE FORM (Field Engineer Role) ---
# =======================================================================
class FEStatusUpdateForm(forms.ModelForm):
    """
    Form used by the Assigned Field Engineer to update job status and upload documents.
    """
    # Network Targets (Made NOT required)
    pop_location_fk = forms.ModelChoiceField(
        queryset=PopLocation.objects.all(),
        required=False,
        label="PoP Location",
        empty_label="--- Select PoP ---"
    )
    switch_fk = forms.ModelChoiceField(
        queryset=Switch.objects.none(),
        required=False,
        label="Switch",
        empty_label="--- Select Switch ---"
    )
    port_number = forms.CharField(
        max_length=20,
        required=False,
        label="Target Port Number"
    )

    # Attachment Fields
    trace_attachment = forms.FileField(required=False)
    splicing_picture = forms.FileField(required=False)

    class Meta:
        model = SplicingJob
        fields = [
            'pop_location_fk',
            'switch_fk',
            'port_number',
            'status',
            'comment',
            'trace_attachment',
            'splicing_picture',
        ]

        widgets = {
            'comment': forms.Textarea(
                attrs={'rows': 3, 'placeholder': 'Enter final completion notes or status update...'}),
        }

    def clean(self):
        cleaned_data = super().clean()
        instance = self.instance

        # Validation: Ensure pop/switch/port are provided if status is the completion status
        status = cleaned_data.get('status')
        pop = cleaned_data.get('pop_location_fk')
        switch = cleaned_data.get('switch_fk')
        port = cleaned_data.get('port_number')

        # Since the status choices use SplicingJob.SERVICE_DELIVERY_PENDING (which is 'SD_PENDING')
        if status == SplicingJob.SERVICE_DELIVERY_PENDING:
            if not pop or not switch or not port:
                self.add_error(None,
                               "PoP Location, Switch, and Port Number are required when marking the job as Completed.")

        # If the instance exists, update it temporarily for model validation
        if instance.pk:
            for field, value in cleaned_data.items():
                setattr(instance, field, value)

        # Run model-level validation check
        try:
            instance.full_clean(exclude=['id', 'job_id'])
        except ValidationError as e:
            for field, errors in e.message_dict.items():
                if field in self.fields:
                    self.add_error(field, errors)
                else:
                    self._errors[forms.NON_FIELD_ERRORS] = self.error_class(errors)

        return cleaned_data

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field.widget, (forms.TextInput, forms.Textarea, forms.Select, forms.FileInput)):
                field.widget.attrs.update({'class': 'w-full'})

        # --- CRITICAL: Use Model Constants for Status Choices ---
        self.fields['status'].choices = [
            (SplicingJob.JOB_IN_PROGRESS, 'Splicing In Progress'),
            (SplicingJob.SERVICE_DELIVERY_PENDING, 'Splicing Completed'),  # Uses model constant ('SD_PENDING')
        ]

        # --- Setup for Chained Dropdown ---
        pop_id = None
        if 'pop_location_fk' in self.data:
            try:
                pop_id = int(self.data.get('pop_location_fk'))
            except (ValueError, TypeError):
                pass
        elif self.instance.pk and self.instance.pop_location_fk:
            pop_id = self.instance.pop_location_fk.pk

        switch_queryset = Switch.objects.none()
        if pop_id:
            switch_queryset = Switch.objects.filter(pop_location_id=pop_id).order_by('name')

        # Ensure the currently selected switch is always in the queryset
        if self.instance.pk and self.instance.switch_fk and self.instance.switch_fk.pk not in switch_queryset.values_list(
                'pk', flat=True):
            switch_queryset = switch_queryset | Switch.objects.filter(pk=self.instance.switch_fk.pk)

        self.fields['switch_fk'].queryset = switch_queryset


# =======================================================================
# --- 4. JOB METADATA UPDATE FORM (Manager/Creator Update) ---
# =======================================================================
class JobMetadataUpdateForm(forms.ModelForm):
    """
    Form for updating core job metadata by Manager/Creator.
    """

    # --- DEFINE NETWORK FIELDS EXPLICITLY ---
    pop_location_fk = forms.ModelChoiceField(
        queryset=PopLocation.objects.all(),
        required=False,
        label="Target PoP Location",
        empty_label="--- Select PoP ---"
    )
    switch_fk = forms.ModelChoiceField(
        queryset=Switch.objects.none(),  # Populated dynamically in __init__
        required=False,
        label="Target Switch",
        empty_label="--- Select Switch ---"
    )

    class Meta:
        model = SplicingJob
        fields = [
            'job_id',
            'customer_name',
            'project_code',
            'required_completion_date',
            'priority',
            'target_duration_hours',

            # --- ADD LOCATION & NETWORK FIELDS TO META.FIELDS ---
            'street_address',
            'neighbourhood',
            'city',
            'province',
            'country',
            'pop_location_fk',
            'switch_fk',
            'port_number',
            # --------------------------------------------------

            'project_manager',
            'civil_contractor_name',
            'description',
            'comment',

            # NEW CONTACT FIELDS for updating
            'contact_person',
            'contact_number',
            'alt_contact_person',
            'alt_contact_number',
        ]

        widgets = {
            'required_completion_date': forms.DateInput(attrs={'type': 'date'}),
            'job_id': forms.TextInput(attrs={'readonly': 'readonly'}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)  # CRITICAL: Capture the current user
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field.widget, (forms.TextInput, forms.Textarea, forms.Select, forms.DateInput)):
                field.widget.attrs.update({'class': 'w-full'})

        # --- Required Completion Date Permission Logic ---
        instance = self.instance
        if instance.pk and instance.required_completion_date and instance.creator and self.user:
            is_creator = self.user and (self.user == instance.creator)
            creator_groups = set(instance.creator.groups.values_list('name', flat=True))
            user_groups = set(self.user.groups.values_list('name', flat=True))
            is_group_member = bool(creator_groups.intersection(user_groups))

            if not (is_creator or is_group_member):
                self.fields['required_completion_date'].widget.attrs['readonly'] = 'readonly'
                self.fields[
                    'required_completion_date'].help_text = "Only the job creator or their group members can modify this date."

        # --- ADD Chained Dropdown Logic ---
        pop_id = None
        if 'pop_location_fk' in self.data:
            try:
                pop_id = int(self.data.get('pop_location_fk'))
            except (ValueError, TypeError):
                pass
        elif self.instance.pk and self.instance.pop_location_fk:
            pop_id = self.instance.pop_location_fk.pk

        switch_queryset = Switch.objects.none()
        if pop_id:
            switch_queryset = Switch.objects.filter(pop_location_id=pop_id).order_by('name')

        self.fields['switch_fk'].queryset = switch_queryset


# =======================================================================
# --- 4.1. JOB CLOSEOUT FORM (Manager/Service Delivery Role) ---
# =======================================================================
class JobCloseoutForm(forms.ModelForm):
    """
    Form for the Manager to provide final closeout comments and archive the job.
    """

    class Meta:
        model = SplicingJob
        fields = ['closeout_comment']

        widgets = {
            'closeout_comment': forms.Textarea(
                attrs={'rows': 5,
                       'placeholder': 'Enter final sign-off notes, archive confirmation, and summary of provisioned service details...'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs.update({'class': 'w-full'})


# =======================================================================
# --- 6. DASHBOARD FILTER/SEARCH FORM ---
# =======================================================================
class JobFilterForm(forms.Form):
    """Form used on the DashboardView to allow users to filter the job list."""
    search_query = forms.CharField(
        required=False,
        label="Search",
        max_length=100,
        widget=forms.TextInput(attrs={
            'placeholder': 'Job ID, City, or Project Code',
            'class': 'w-full border-2 border-blue-500 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500'
        })
    )

    assigned_fe = forms.ModelChoiceField(
        queryset=get_field_engineers(),
        required=False,
        label="Assigned FE",
        empty_label="All Engineers",
        widget=forms.Select(attrs={'class': 'rounded-md'})
    )

    status = forms.ChoiceField(
        choices=[('', 'All Statuses')] + SplicingJob.STATUS_CHOICES,
        required=False,
        label="Status",
        widget=forms.Select(attrs={'class': 'rounded-md'})
    )

    priority = forms.ChoiceField(
        choices=[('', 'All Priorities')] + SplicingJob.PRIORITY_CHOICES,
        required=False,
        label="Priority",
        widget=forms.Select(attrs={'class': 'rounded-md'})
    )


# =======================================================================
# --- 7. CONTRACTOR STATUS UPDATE FORM (External Contractor Role) ---
# =======================================================================
class ContractorStatusUpdateForm(forms.ModelForm):
    """
    Form used by the External Splicing Contractor to update job status.
    OTDR traces and Splicing photos are now OPTIONAL for closing the job.
    """
    status = forms.ChoiceField(
        choices=CONTRACTOR_ALLOWED_STATUSES,
        widget=forms.Select(),
        label="Select New Job Status"
    )

    pop_location_fk = forms.ModelChoiceField(
        queryset=PopLocation.objects.all(),
        required=False,
        label="PoP Location (Network Target)",
        empty_label="--- Select PoP ---"
    )
    switch_fk = forms.ModelChoiceField(
        queryset=Switch.objects.none(),
        required=False,
        label="Switch (Network Target)",
        empty_label="--- Select Switch ---"
    )
    port_number = forms.CharField(
        max_length=20,
        required=False,
        label="Target Port Number"
    )

    trace_attachment = forms.FileField(required=False, label="OTDR Trace File (Optional)")
    splicing_picture = forms.FileField(required=False, label="Splicing Photo (Optional)")

    class Meta:
        model = SplicingJob
        fields = [
            'pop_location_fk',
            'switch_fk',
            'port_number',
            'status',
            'comment',
            'trace_attachment',
            'splicing_picture',
        ]
        widgets = {
            'comment': forms.Textarea(
                attrs={'rows': 4, 'placeholder': 'Enter job status update, notes, and details on completion...'}),
        }

    def clean(self):
        cleaned_data = super().clean()
        instance = self.instance
        status = cleaned_data.get('status')

        is_marking_complete = (status == 'COMPLETED')

        # --- FIX 1: Retain disabled instance values ---
        if instance.pop_location_fk and 'pop_location_fk' not in self.data:
            cleaned_data['pop_location_fk'] = instance.pop_location_fk
        if instance.switch_fk and 'switch_fk' not in self.data:
            cleaned_data['switch_fk'] = instance.switch_fk

        # --- FIX 2: Swap for Validation ---
        if is_marking_complete:
            cleaned_data['status'] = SplicingJob.SERVICE_DELIVERY_PENDING

        # Re-read status after swap
        current_status = cleaned_data.get('status')

        # --- Updated Validation Block ---
        if current_status == SplicingJob.SERVICE_DELIVERY_PENDING:
            # NOTE: Attachment Checks for trace_attachment and splicing_picture have been REMOVED.

            # Network Field Check (Still required for connectivity records)
            pop = cleaned_data.get('pop_location_fk')
            switch = cleaned_data.get('switch_fk')
            port = cleaned_data.get('port_number')
            if not pop or not switch or not port:
                self.add_error(None,
                               "PoP Location, Switch, and Port Number are required when marking the job as Completed.")

        # --- FIX 3: Restore choice for view ---
        if is_marking_complete and not self.errors:
            cleaned_data['status'] = 'COMPLETED'

        return cleaned_data

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = self.instance
        pop_id = None

        if instance and instance.pop_location_fk:
            pop_id = instance.pop_location_fk.pk
            self.fields['pop_location_fk'].widget.attrs['disabled'] = 'disabled'
            if instance.switch_fk:
                self.fields['switch_fk'].widget.attrs['disabled'] = 'disabled'

        if pop_id is None and 'pop_location_fk' in self.data:
            try:
                pop_id = int(self.data.get('pop_location_fk'))
            except (ValueError, TypeError):
                pass

        switch_queryset = Switch.objects.none()
        if pop_id:
            switch_queryset = Switch.objects.filter(pop_location_id=pop_id).order_by('name')

        if instance and instance.switch_fk and instance.switch_fk.pk not in switch_queryset.values_list('pk',
                                                                                                        flat=True):
            switch_queryset = switch_queryset | Switch.objects.filter(pk=instance.switch_fk.pk)

        self.fields['switch_fk'].queryset = switch_queryset

        for name, field in self.fields.items():
            if isinstance(field.widget, (forms.TextInput, forms.Textarea, forms.Select, forms.FileInput)):
                field.widget.attrs.update({'class': 'w-full'})


# =======================================================================
# --- 8. SERVICE PROVISIONING FORM (Service Delivery Team Role) ---
# =======================================================================
class ProvisioningRecordForm(forms.ModelForm):
    """
    Form used by the Service Delivery team to input network configuration details.
    """
    capacity_mbps = forms.IntegerField(
        required=False,
        label="Capacity (Mbps)"
    )

    class Meta:
        model = ProvisioningRecord
        fields = [
            'vlan_id',
            'ip_address',
            'subnet_mask',
            'gateway_ip',
            'service_type',
            'capacity_mbps',
            'final_service_notes',
        ]

        widgets = {
            'ip_address': forms.TextInput(attrs={'placeholder': 'e.g., 192.168.1.10'}),
            'subnet_mask': forms.TextInput(attrs={'placeholder': 'e.g., 255.255.255.0 or /24'}),
            'gateway_ip': forms.TextInput(attrs={'placeholder': 'e.g., 192.168.1.1'}),
            'final_service_notes': forms.Textarea(
                attrs={'rows': 4,
                       'placeholder': 'Detailed description of the service configured (e.g., bandwidth, routing details)...'}),
        }

    def clean_ip_address(self):
        ip = self.cleaned_data['ip_address']
        # Simple validation: ensure it looks somewhat like an IP address
        if ip and not all(c.isdigit() or c == '.' for c in ip):
            raise forms.ValidationError("Please enter a valid IP address format.")
        return ip

    def clean_gateway_ip(self):
        ip = self.cleaned_data['gateway_ip']
        # Simple validation: ensure it looks somewhat like an IP address
        if ip and not all(c.isdigit() or c == '.' for c in ip):
            raise forms.ValidationError("Please enter a valid Gateway IP address format.")
        return ip

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field.widget, (forms.TextInput, forms.Textarea, forms.Select)):
                field.widget.attrs.update({'class': 'w-full'})