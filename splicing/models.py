from datetime import timedelta
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db.models import F, ExpressionWrapper, DurationField

# --- 0. EXTENDED USER PROFILE MODEL ---
class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    contractor_company_name = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name="Contractor Company Name (for Dashboard Filter)"
    )

    def __str__(self):
        return f"Profile for {self.user.username}"


# --- 1. NETWORK INVENTORY MODELS ---
class PopLocation(models.Model):
    name = models.CharField(max_length=100, unique=True)
    address = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "PoP Location"
        verbose_name_plural = "PoP Locations"

    def __str__(self):
        return self.name


class Switch(models.Model):
    pop_location = models.ForeignKey(PopLocation, on_delete=models.CASCADE, related_name='switches')
    name = models.CharField(max_length=100)
    serial_number = models.CharField(max_length=100, blank=True)

    class Meta:
        unique_together = ('pop_location', 'name')
        verbose_name_plural = "Switches"

    def __str__(self):
        return f'{self.pop_location.name} - {self.name}'


# --- 2. SPLICING JOB MODEL ---
class SplicingJob(models.Model):
    # --- STATUS CHOICES ---
    JOB_DRAFT = 'NEW'
    MANAGER_ASSIGNED = 'MANAGER_ASSIGNED'
    FE_ASSIGNED = 'FE_ASSIGNED'
    JOB_IN_PROGRESS = 'IN_PROGRESS'
    JOB_ON_HOLD = 'ON_HOLD'
    SERVICE_DELIVERY_PENDING = 'SD_PENDING'
    JOB_PROVISIONED = 'PROVISIONED'
    JOB_CLOSED_ARCHIVED = 'CLOSED_ARCHIVED'
    JOB_CANCELLED = 'CANCELLED'

    STATUS_CHOICES = [
        (JOB_DRAFT, 'New/Draft Job'),
        (MANAGER_ASSIGNED, 'Assigned to Manager'),
        (FE_ASSIGNED, 'Assigned to Field Engineer'),
        (JOB_IN_PROGRESS, 'Splicing In Progress'),
        (JOB_ON_HOLD, 'Job On Hold'),
        (SERVICE_DELIVERY_PENDING, 'Ready for Service Provisioning'),
        (JOB_PROVISIONED, 'Service Provisioned (Complete)'),
        (JOB_CLOSED_ARCHIVED, 'Job Closed and Archived'),
        (JOB_CANCELLED, 'Cancelled'),
    ]

    PRIORITY_CHOICES = [(1, 'High'), (2, 'Medium'), (3, 'Low')]

    # --- CORE JOB METADATA ---
    job_id = models.CharField(max_length=50, unique=True)
    project_code = models.CharField(max_length=50, blank=True)
    circuit_id = models.CharField(
        max_length=100,
        unique=True,
        null=True,
        blank=True,
        help_text="Enter the unique Circuit ID (e.g., LSK-FTTH-1234)"
    )
    customer_name = models.CharField(max_length=100, blank=True, verbose_name="Customer Name")
    priority = models.IntegerField(choices=PRIORITY_CHOICES, default=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=JOB_DRAFT)
    description = models.TextField(verbose_name="Job Scope/Description", blank=True)

    # --- CONTACT INFORMATION ---
    contact_person = models.CharField(max_length=100, blank=True, verbose_name="Primary Contact Person")
    contact_number = models.CharField(max_length=20, blank=True, verbose_name="Primary Contact Number")
    alt_contact_person = models.CharField(max_length=100, blank=True, verbose_name="Alternative Contact Person")
    alt_contact_number = models.CharField(max_length=20, blank=True, verbose_name="Alternative Contact Number")

    # --- NETWORK TARGETS ---
    pop_location_fk = models.ForeignKey(PopLocation, on_delete=models.SET_NULL, null=True, blank=True, related_name='jobs')
    switch_fk = models.ForeignKey(Switch, on_delete=models.SET_NULL, null=True, blank=True, related_name='jobs')
    port_number = models.CharField(max_length=20, blank=True, verbose_name="Target Port Number")

    # --- GEOGRAPHICAL FIELDS ---
    street_address = models.CharField(max_length=255, blank=True)
    neighbourhood = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=100, blank=True)
    province = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=50, default='Zambia', blank=True)

    # --- PERSONNEL ---
    project_manager = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='managed_projects')
    civil_contractor_name = models.CharField(max_length=100, blank=True)
    splicing_contractor_company = models.CharField(max_length=100, blank=True, null=True)
    creator = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_jobs')
    assigned_manager = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='managed_jobs')
    assigned_fe = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='fe_jobs')

    # --- TIMING ---
    start_date = models.DateTimeField(auto_now_add=True)
    end_date = models.DateTimeField(null=True, blank=True)
    required_completion_date = models.DateField(null=True, blank=True)
    target_duration_hours = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    # --- NOTES AND ATTACHMENTS ---
    comment = models.TextField(blank=True)
    trace_attachment = models.FileField(upload_to='job_traces/', null=True, blank=True)
    splicing_picture = models.ImageField(upload_to='splicing_photos/', null=True, blank=True)
    closeout_comment = models.TextField(blank=True, verbose_name="Manager Closeout Comment")

    class Meta:
        verbose_name = "Splicing Job"
        verbose_name_plural = "Splicing Jobs"
        permissions = [
            ("can_view_advanced_report", "Can view the advanced dashboard and exports"),
        ]

    def __str__(self):
        return f'{self.job_id} - {self.neighbourhood}, {self.city}'

    # --- NEW: DYNAMIC AGE CALCULATION ---
    @property
    def age_days(self):
        if self.start_date:
            # Use the end_date if closed, otherwise use "now"
            end_point = self.end_date if self.end_date else timezone.now()

            # Use .date() to compare calendar days instead of full 24-hour durations
            delta = end_point.date() - self.start_date.date()
            return delta.days
        return 0

    @property
    def completion_duration(self):
        if self.start_date and self.end_date:
            return self.end_date - self.start_date
        return None

    def save(self, *args, **kwargs):
        # Automatically set end_date when hitting these final milestones
        final_statuses = [self.JOB_PROVISIONED, self.JOB_CLOSED_ARCHIVED, self.SERVICE_DELIVERY_PENDING]
        if self.status in final_statuses and not self.end_date:
            if self.pk:
                original = SplicingJob.objects.get(pk=self.pk)
                if original.status not in final_statuses:
                    self.end_date = timezone.now()
            else:
                self.end_date = timezone.now()
        super().save(*args, **kwargs)

    def is_overdue(self):
        work_stopped_statuses = [self.SERVICE_DELIVERY_PENDING, self.JOB_PROVISIONED, self.JOB_CLOSED_ARCHIVED, self.JOB_ON_HOLD]
        if self.required_completion_date and self.status not in work_stopped_statuses:
            return self.required_completion_date < timezone.now().date()
        return False

    def time_remaining_or_overdue(self):
        work_stopped_statuses = [self.SERVICE_DELIVERY_PENDING, self.JOB_PROVISIONED, self.JOB_CLOSED_ARCHIVED, self.JOB_ON_HOLD]
        if self.required_completion_date and self.status not in work_stopped_statuses:
            today = timezone.now().date()
            delta = self.required_completion_date - today
            return f'{delta.days} days remaining' if delta.days >= 0 else f'{-delta.days} days overdue'
        return "N/A"


# --- 3. SERVICE PROVISIONING RECORD MODEL ---
class ProvisioningRecord(models.Model):
    SERVICE_TYPE_CHOICES = [
        ('LEASING', 'Fibre Leasing'),
        ('INTERNET', 'Dedicated Internet'),
        ('VPN', 'MPLS VPN'),
        ('OTHER', 'Other')
    ]

    splicing_job = models.OneToOneField(SplicingJob, on_delete=models.CASCADE, related_name='provisioning_record')
    vlan_id = models.CharField(max_length=10, blank=True, null=True)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    subnet_mask = models.CharField(max_length=15, blank=True, null=True)
    gateway_ip = models.GenericIPAddressField(blank=True, null=True)
    service_type = models.CharField(max_length=50, choices=SERVICE_TYPE_CHOICES, default='INTERNET')
    capacity_mbps = models.IntegerField(null=True, blank=True)
    final_service_notes = models.TextField(blank=True, null=True)
    configured_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='provisioned_jobs')
    configured_on = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Service Provisioning Record"
        verbose_name_plural = "Service Provisioning Records"

    def __str__(self):
        return f"Provisioning for Job {self.splicing_job.job_id}"