from django.contrib import admin
from .models import (
    SplicingJob,
    ProvisioningRecord,  # <--- ADDED: Assuming you want to manage ProvisioningRecords
    PopLocation,
    Switch,
    UserProfile
)


# --- 0. EXTENDED USER PROFILE ADMIN ---
@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'contractor_company_name',
        'is_contractor'
    )
    search_fields = (
        'user__username',
        'user__first_name',
        'user__last_name',
        'contractor_company_name'
    )

    # Optional: Define a method to show if the user is a contractor for filtering/display
    def is_contractor(self, obj):
        # Checks if the user associated with this profile is in the Contractors group
        return obj.user.groups.filter(name='Contractors').exists()

    is_contractor.boolean = True
    is_contractor.short_description = 'Is Contractor User'

    # Use a model field in list_filter
    list_filter = ('contractor_company_name',)


# --- 1. SPLICING JOB ADMIN (Core Job) ---
@admin.register(SplicingJob)
class SplicingJobAdmin(admin.ModelAdmin):
    # Field sets for better organization in the detail view
    fieldsets = (
        ('CORE METADATA', {
            'fields': (('job_id', 'priority'), 'project_code', 'customer_name', 'description')
        }),
        ('NETWORK TARGETS (Customer Side)', {
            # New contact fields included here
            'fields': (
                ('contact_person', 'contact_number'),
                ('alt_contact_person', 'alt_contact_number')
            )
        }),
        ('NETWORK TARGETS (PoP Side)', {
            # Inventory fields included here
            'fields': ('pop_location_fk', 'switch_fk', 'port_number')
        }),
        ('GEOGRAPHICAL DATA', {
            'fields': ('street_address', 'neighbourhood', ('city', 'province', 'country'))
        }),
        ('PERSONNEL & STATUS', {
            'fields': (
                ('status', 'required_completion_date'),
                'creator', 'project_manager',
                ('assigned_manager', 'assigned_fe'),
                'civil_contractor_name', 'splicing_contractor_company'
            )
        }),
        ('DATES & CLOSEOUT', {
            'fields': ('start_date', 'end_date', 'target_duration_hours', 'comment', 'closeout_comment')
        }),
        ('ATTACHMENTS', {
            'fields': ('trace_attachment', 'splicing_picture')
        }),
    )

    list_display = (
        'job_id',
        'customer_name',  # Added customer name for quick view
        'project_code',
        'city',
        'priority',
        'status',
        'assigned_fe',
        'required_completion_date',
        'start_date'
    )
    list_filter = ('status', 'priority', 'assigned_fe', 'project_manager', 'pop_location_fk')
    search_fields = (
        'job_id',
        'customer_name',
        'city',
        'neighbourhood',
        'project_code',
        'splicing_contractor_company'
    )
    date_hierarchy = 'start_date'
    # Make these read-only in the admin, the view/form logic handles the updates
    readonly_fields = ('start_date', 'end_date', 'creator')


# --- 2. SERVICE PROVISIONING ADMIN ---
# (Added since this model is essential for your SD dashboard)
@admin.register(ProvisioningRecord)
class ProvisioningRecordAdmin(admin.ModelAdmin):
    list_display = (
        'splicing_job',
        'service_type',
        'vlan_id',
        'ip_address',
        'configured_by',
        'configured_on'
    )
    list_filter = ('service_type', 'configured_by')
    search_fields = (
        'splicing_job__job_id',
        'ip_address',
        'final_service_notes'
    )
    # The record should only be created/edited via the job page or the SD view,
    # but having it here for direct management is useful.
    raw_id_fields = ('splicing_job', 'configured_by')


# --- REMOVED: Manhole, Cable, and SplicePerformance Admin registrations ---


# --- 3. NETWORK INVENTORY MODELS ---

@admin.register(PopLocation)
class PopLocationAdmin(admin.ModelAdmin):
    list_display = ('name', 'address')
    search_fields = ('name', 'address')


@admin.register(Switch)
class SwitchAdmin(admin.ModelAdmin):
    list_display = ('name', 'serial_number', 'pop_location')
    list_filter = ('pop_location',)
    search_fields = ('name', 'serial_number')