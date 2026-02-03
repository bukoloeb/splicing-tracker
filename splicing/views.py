from django.shortcuts import render, get_object_or_404, redirect
from django.views.generic import ListView, DetailView, CreateView, UpdateView, View, DeleteView, TemplateView
from django.urls import reverse_lazy, reverse
from django.db import transaction, models
from django.contrib import messages
from django.contrib.auth.models import User, Group
from django.http import HttpResponseRedirect, JsonResponse, HttpResponse
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.decorators import login_required, user_passes_test
from django.utils import timezone
from django.db.models import F, ExpressionWrapper, DurationField, Count, Avg, Q, Case, When, Value
from django.db.models.functions import Coalesce, TruncMonth
from django.template.loader import render_to_string
import uuid
import pytz
from datetime import timedelta, date
from weasyprint import HTML
from openpyxl import Workbook

# --- 1. Corrected Model Imports (Forms removed from here) ---
from .models import (
    SplicingJob,
    PopLocation,
    Switch,
    UserProfile,
    ProvisioningRecord
)

# --- 2. Application Form Imports (JobFilterForm lives here) ---
from .forms import (
    SplicingJobCreationForm,
    JobAssignmentForm,
    FEStatusUpdateForm,
    JobFilterForm,
    JobMetadataUpdateForm,
    ContractorStatusUpdateForm,
    ProvisioningRecordForm,
    JobCloseoutForm,
)
# ====================================================================
# 1. HELPER FUNCTIONS
# ====================================================================

def generate_job_id():
    """Generates a unique, short Job ID."""
    return str(uuid.uuid4())[:8].upper()

def is_manager(user):
    return user.groups.filter(name='Splicing_Managers').exists() or user.is_superuser

def is_technical_manager(user):
    """Checks if the user belongs to the 'Technical_Managers' group."""
    return user.groups.filter(name='Technical_Managers').exists() or user.is_superuser

# ====================================================================
# 2. TECHNICAL MANAGER REPORT VIEW
# ====================================================================

class TechnicalManagerReportView(LoginRequiredMixin, TemplateView):
    template_name = 'splicing/technical_manager_report.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        now = timezone.now()

        # 1. Base Queryset
        jobs_qs = SplicingJob.objects.all().select_related('assigned_fe')

        # 2. Filter Logic
        form = JobFilterForm(self.request.GET)
        if form.is_valid():
            search = form.cleaned_data.get('search_query')
            status_f = form.cleaned_data.get('status')
            fe_f = form.cleaned_data.get('assigned_fe')
            priority_f = form.cleaned_data.get('priority')

            if search:
                jobs_qs = jobs_qs.filter(
                    Q(job_id__icontains=search) | Q(customer_name__icontains=search)
                )
            if status_f:
                jobs_qs = jobs_qs.filter(status=status_f)
            if fe_f:
                jobs_qs = jobs_qs.filter(assigned_fe=fe_f)
            if priority_f:
                jobs_qs = jobs_qs.filter(priority=priority_f)

        # 3. Velocity Stats
        context['splicing_progress'] = {
            'today': SplicingJob.objects.filter(end_date__date=now.date()).count(),
            'wtd': SplicingJob.objects.filter(end_date__gte=now - timedelta(days=7)).count(),
            'mtd': SplicingJob.objects.filter(end_date__month=now.month, end_date__year=now.year).count(),
            'ytd': SplicingJob.objects.filter(end_date__year=now.year).count(),
            'active_in_field': SplicingJob.objects.filter(
                status__in=['FE_ASSIGNED', 'IN_PROGRESS', 'SD_PENDING']).count(),
        }

        # 4. Resource Metrics
        resource_metrics = []
        assigned_users = User.objects.filter(fe_jobs__in=jobs_qs).distinct()
        for fe in assigned_users:
            fe_jobs = jobs_qs.filter(assigned_fe=fe)
            active_list = fe_jobs.filter(status__in=['SD_PENDING', 'PROVISIONED', 'IN_PROGRESS', 'FE_ASSIGNED'])
            ytd_done = fe_jobs.filter(status__in=['PROVISIONED', 'CLOSED_ARCHIVED'], end_date__year=now.year).count()

            resource_metrics.append({
                'name': fe.get_full_name() or fe.username,
                'active_count': active_list.count(),
                'active_jobs': active_list[:3],
                'ytd_done': ytd_done,
                'efficiency': round((ytd_done / fe_jobs.count() * 100), 1) if fe_jobs.exists() else 0
            })
        context['resource_metrics'] = sorted(resource_metrics, key=lambda x: x['active_count'], reverse=True)

        # 5. Pipeline Table (THE AGE FIX)
        # We convert to list and MANUALLY calculate age_days for the template
        display_list = list(jobs_qs.order_by('-start_date')[:100])
        for job in display_list:
            if job.start_date:
                end_point = job.end_date if job.end_date else now
                job.age_days = (end_point - job.start_date).days
            else:
                job.age_days = 0

        context['jobs_list'] = display_list
        context['filter_form'] = form
        return context

def is_advanced_reporter(user):
    """
    Checks if the user has been granted the specific permission in Django Admin
    OR is a superuser. String format: 'app_label.codename'
    """
    if not user.is_authenticated:
        return False

    # 'splicing' is likely your app name (based on your model file)
    # 'can_view_advanced_report' is the permission codename defined in models.py
    return user.has_perm('splicing.can_view_advanced_report') or user.is_superuser


def can_view_advanced_report(user):
    """
    Alias for the above check, used for template logic and decorators.
    """
    return is_advanced_reporter(user)


def is_field_engineer(user):
    return user.groups.filter(name='Field_Engineers').exists()


def is_job_creator(user):
    return user.groups.filter(name='Job_Creators').exists()


def is_job_viewer(user):
    """Checks if the user belongs to the 'Viewer' group."""
    return user.groups.filter(name='Viewer').exists() or user.is_superuser


def is_contractor(user):
    return user.groups.filter(name='Contractors').exists()


def is_service_delivery(user):
    """Checks if the user belongs to the 'Service_Delivery' group."""
    return user.groups.filter(name='Service_Delivery').exists() or user.is_superuser


def can_view_advanced_report(user):
    # Option A: Check if they are in a specific group
    # return user.groups.filter(name='Report Viewers').exists()

    # Option B: Check if they are staff or superuser
    return user.is_staff or user.groups.filter(name='Management').exists()



# ====================================================================
# 1B. CUSTOM LOGIN REDIRECT (FINALIZED)
# ====================================================================

@login_required
@login_required
@login_required
def custom_dashboard_redirect(request):
    """
    Checks the user's role and redirects them to the most appropriate dashboard.
    Primary roles take priority so users land on their working dashboard.
    """
    user = request.user

    # 1. Functional Roles (Primary Dashboards)
    if is_manager(user):
        messages.info(request, "Redirecting to Splicing Manager Dashboard.")
        return redirect('job_dashboard')

    if is_service_delivery(user):
        # Based on your requirements, SD team focuses on 'Job Closed and Archived'
        messages.info(request, "Redirecting to Service Delivery Dashboard.")
        return redirect('sd_dashboard')

    if is_technical_manager(user):
        messages.info(request, "Redirecting to Technical Manager Report View.")
        return redirect('tech_manager_report')

    if is_field_engineer(user) or is_job_creator(user):
        return redirect('job_dashboard')

    if is_contractor(user):
        return redirect('contractor_dashboard')

    if is_job_viewer(user):
        return redirect('job_viewer_dashboard')

    # 2. Secondary/Permission-Based Roles
    # Only redirect here if they have NO other functional role assigned
    if is_advanced_reporter(user):
        messages.info(request, "Redirecting to Advanced Report View.")
        return redirect('advanced_report')

    # 3. Fallback
    return redirect('job_dashboard')


# ====================================================================
# 2. MIXINS FOR PERMISSION CONTROL (UPDATED)
# ====================================================================

class JobCreatorRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        return is_job_creator(self.request.user) or self.request.user.is_superuser


class ManagerRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        return is_manager(self.request.user)


class AdvancedReporterRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        return is_advanced_reporter(self.request.user)


# ====================================================================
# 3. CORE JOB WORKFLOW VIEWS
# ====================================================================


class DashboardView(LoginRequiredMixin, ListView):
    model = SplicingJob
    template_name = 'splicing/manager_dashboard.html'
    context_object_name = 'jobs'
    ordering = ['priority', '-start_date']

    def get_queryset(self):
        user = self.request.user
        queryset = SplicingJob.objects.all().select_related('assigned_fe')
        filter_form = JobFilterForm(self.request.GET)

        # 1. Base Queryset Filtering (By Role)
        if is_field_engineer(user) and not (is_manager(user) or is_technical_manager(user)):
            queryset = queryset.filter(assigned_fe=user)
        elif is_contractor(user) and not is_service_delivery(user):
            try:
                company = user.profile.contractor_company_name
                queryset = queryset.filter(splicing_contractor_company__iexact=company) if company else queryset.none()
            except:
                queryset = queryset.none()
        elif is_job_creator(user) and not (is_manager(user) or is_technical_manager(user)):
            queryset = queryset.filter(creator=user)

        # 2. Advanced Filtering
        if filter_form.is_valid():
            data = filter_form.cleaned_data
            if data.get('search_query'):
                q = data.get('search_query')
                queryset = queryset.filter(Q(job_id__icontains=q) | Q(customer_name__icontains=q))
            if data.get('status'):
                queryset = queryset.filter(status=data.get('status'))
            if data.get('assigned_fe'):
                queryset = queryset.filter(assigned_fe=data.get('assigned_fe'))

        return queryset.annotate(
            job_age=ExpressionWrapper(timezone.now() - F('start_date'), output_field=DurationField())
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        now = timezone.now()

        # Base stats for the top cards
        stats_qs = SplicingJob.objects.all()

        # KPIs: STATUS BASED
        context['pending_assignment'] = stats_qs.filter(status='PENDING_ASSIGNMENT').count()
        context['active_splicing'] = stats_qs.filter(status__in=['FE_ASSIGNED', 'IN_PROGRESS']).count()
        context['pending_provisioning'] = stats_qs.filter(status__in=['SD_PENDING', 'CLOSED_ARCHIVED']).count()

        # KPIs: TIME BASED
        context['metrics'] = {
            'wtd': stats_qs.filter(end_date__gte=now - timedelta(days=7)).count(),
            'mtd': stats_qs.filter(end_date__month=now.month, end_date__year=now.year).count(),
            'ytd': stats_qs.filter(end_date__year=now.year).count(),
        }

        # --- WORKLOAD AGGREGATION ---
        if is_manager(user) or is_technical_manager(user):
            # 1. Internal FE Workload (Users)
            context['fe_active_workload'] = User.objects.annotate(
                job_count=Count('fe_jobs', filter=Q(fe_jobs__status__in=['FE_ASSIGNED', 'IN_PROGRESS', 'SD_PENDING']))
            ).filter(job_count__gt=0).order_by('-job_count')

            # 2. Contractor Workload (Company Strings)
            context['contractor_workload'] = stats_qs.filter(
                status__in=['FE_ASSIGNED', 'IN_PROGRESS', 'SD_PENDING']
            ).exclude(
                splicing_contractor_company__isnull=True
            ).exclude(
                splicing_contractor_company=''
            ).values('splicing_contractor_company').annotate(
                job_count=Count('id')
            ).order_by('-job_count')

        context['filter_form'] = JobFilterForm(self.request.GET)
        return context

class JobCreateView(LoginRequiredMixin, JobCreatorRequiredMixin, CreateView):
    model = SplicingJob
    form_class = SplicingJobCreationForm
    template_name = 'splicing/job_create.html'

    def form_valid(self, form):
        form.instance.creator = self.request.user
        form.instance.status = SplicingJob.MANAGER_ASSIGNED
        form.instance.job_id = generate_job_id()
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('job_detail', kwargs={'job_id': self.object.job_id})

class JobViewerView(LoginRequiredMixin, UserPassesTestMixin, ListView):
    """Read-only dashboard for users who only need to check job status."""
    model = SplicingJob
    template_name = 'splicing/job_viewer_dashboard.html'
    context_object_name = 'jobs'
    ordering = ['-start_date']
    paginate_by = 20

    def test_func(self):
        return is_job_viewer(self.request.user)

    def get_queryset(self):
        # Define statuses excluded from the general viewer dashboard (final/completed statuses)
        excluded_statuses = [
            SplicingJob.JOB_CLOSED_ARCHIVED,
            SplicingJob.JOB_PROVISIONED
        ]

        # Start with jobs not in the final excluded statuses
        queryset = SplicingJob.objects.exclude(status__in=excluded_statuses)

        filter_form = JobFilterForm(self.request.GET)
        if filter_form.is_valid():
            data = filter_form.cleaned_data

            search_query = data.get('search_query')
            if search_query:
                queryset = queryset.filter(
                    Q(job_id__icontains=search_query) |
                    Q(project_code__icontains=search_query) |
                    Q(customer_name__icontains=search_query) |
                    Q(city__icontains=search_query)
                )

            status = data.get('status')
            if status:
                queryset = queryset.filter(status=status)

            priority = data.get('priority')
            if priority:
                queryset = queryset.filter(priority=priority)

            assigned_fe = data.get('assigned_fe')
            if assigned_fe:
                queryset = queryset.filter(assigned_fe=assigned_fe)

        return queryset.order_by('-start_date')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['filter_form'] = JobFilterForm(self.request.GET)
        return context

class JobDetailView(LoginRequiredMixin, DetailView):
    model = SplicingJob
    context_object_name = 'job'
    template_name = 'splicing/job_detail.html'
    slug_field = 'job_id'
    slug_url_kwarg = 'job_id'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        # Fetch user's groups in one query
        user_groups = set(user.groups.values_list('name', flat=True))
        is_superuser = user.is_superuser

        # Helper lambda to check membership against the fetched set of groups
        def check_role(role_name):
            return is_superuser or (role_name in user_groups)

        # Assign Role Context
        context['is_manager'] = check_role('Splicing_Managers')
        context['is_fe'] = check_role('Field_Engineers')
        context['is_creator'] = check_role('Job_Creators')
        context['is_contractor'] = check_role('Contractors')

        context['is_service_delivery'] = check_role('Service_Delivery')

        context['is_job_viewer'] = check_role('Viewer')
        context['is_technical_manager'] = check_role('Technical_Managers')
        context['is_advanced_reporter'] = check_role('Advanced_Reporters')

        # Provisioning Record Context
        try:
            # Note: self.object is the SplicingJob instance
            context['provisioning_record'] = self.object.provisioning_record
        except ProvisioningRecord.DoesNotExist:
            context['provisioning_record'] = None

        context['job'] = self.object
        return context

class JobAssignmentView(LoginRequiredMixin, ManagerRequiredMixin, UpdateView):
    model = SplicingJob
    form_class = JobAssignmentForm
    template_name = 'splicing/job_assign.html'
    slug_field = 'job_id'
    slug_url_kwarg = 'job_id'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['job'] = self.object
        return context

    # --- CRITICAL ADDITION: Pass the user to the form ---
    def get_form_kwargs(self):
        """Passes the current request user to the form for filtering choices."""
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user  # <--- THIS FIXES THE ATTRIBUTE ERROR
        return kwargs
    # ---------------------------------------------------

    def form_valid(self, form):
        if not form.instance.assigned_manager:
            form.instance.assigned_manager = self.request.user

        assigned_fe = form.cleaned_data.get('assigned_fe')
        contractor_name = form.cleaned_data.get('splicing_contractor_company')

        if assigned_fe or contractor_name:
            form.instance.status = SplicingJob.FE_ASSIGNED
        else:
            form.instance.status = SplicingJob.MANAGER_ASSIGNED

        messages.success(self.request, f"Job {self.object.job_id} assignment updated successfully.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('job_detail', kwargs={'job_id': self.object.job_id})


class TechnicalManagerReportView(LoginRequiredMixin, TemplateView):
    template_name = 'splicing/technical_manager_report.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        now = timezone.now()

        # 1. FETCH BASE DATA
        jobs_qs = SplicingJob.objects.all().select_related('assigned_fe')

        # 2. APPLY FILTERS
        form = JobFilterForm(self.request.GET)
        if form.is_valid():
            search = form.cleaned_data.get('search_query')
            status_f = form.cleaned_data.get('status')
            fe_f = form.cleaned_data.get('assigned_fe')

            if search:
                jobs_qs = jobs_qs.filter(
                    Q(job_id__icontains=search) | Q(customer_name__icontains=search)
                )
            if status_f:
                jobs_qs = jobs_qs.filter(status=status_f)
            if fe_f:
                jobs_qs = jobs_qs.filter(assigned_fe=fe_f)

        # 3. STATS
        context['splicing_progress'] = {
            'today': SplicingJob.objects.filter(end_date__date=now.date()).count(),
            'wtd': SplicingJob.objects.filter(end_date__gte=now - timedelta(days=7)).count(),
            'mtd': SplicingJob.objects.filter(end_date__month=now.month).count(),
            'ytd': SplicingJob.objects.filter(end_date__year=now.year).count(),
            'active_in_field': SplicingJob.objects.filter(
                status__in=['FE_ASSIGNED', 'IN_PROGRESS', 'SD_PENDING', 'PROVISIONED']
            ).count(),
        }

        # 4. UNIFIED RESOURCE MATRIX (Engineers + Contractors)
        resource_metrics = []
        active_statuses = ['FE_ASSIGNED', 'IN_PROGRESS', 'SD_PENDING', 'PROVISIONED']
        closed_statuses = ['PROVISIONED', 'CLOSED_ARCHIVED']

        # A. Process Internal Engineers (Users)
        assigned_users = User.objects.filter(fe_jobs__in=jobs_qs).distinct()
        for fe in assigned_users:
            fe_jobs = jobs_qs.filter(assigned_fe=fe)
            active_list = fe_jobs.filter(status__in=active_statuses)

            total_assigned = fe_jobs.count()
            completed_count = fe_jobs.filter(status__in=closed_statuses).count()
            efficiency = round((completed_count / total_assigned * 100), 1) if total_assigned > 0 else 0

            resource_metrics.append({
                'name': fe.get_full_name() or fe.username,
                'active_count': active_list.count(),
                'active_jobs': active_list[:3],
                'ytd_done': completed_count,
                'efficiency': efficiency,
                'is_contractor': False
            })

        # B. Process Contractors (Company String)
        contractors = jobs_qs.exclude(
            Q(splicing_contractor_company__isnull=True) | Q(splicing_contractor_company='')
        ).values('splicing_contractor_company').annotate(
            total=Count('id'),
            active=Count('id', filter=Q(status__in=active_statuses)),
            done=Count('id', filter=Q(status__in=closed_statuses))
        )

        for con in contractors:
            company_name = con['splicing_contractor_company']
            efficiency = round((con['done'] / con['total'] * 100), 1) if con['total'] > 0 else 0

            resource_metrics.append({
                'name': company_name,
                'active_count': con['active'],
                'active_jobs': jobs_qs.filter(splicing_contractor_company=company_name, status__in=active_statuses)[:3],
                'ytd_done': con['done'],
                'efficiency': efficiency,
                'is_contractor': True
            })

        context['resource_metrics'] = sorted(resource_metrics, key=lambda x: x['active_count'], reverse=True)

        # 5. FINAL LIST (Removed the age_days assignment loop to prevent AttributeError)
        context['jobs_list'] = jobs_qs.order_by('-start_date')
        context['filter_form'] = form
        context['sd_backlog_count'] = SplicingJob.objects.filter(status='CLOSED_ARCHIVED').count()

        return context

##end of advanced report

###service delivery Dashboard view
class ServiceDeliveryDashboardView(LoginRequiredMixin, UserPassesTestMixin, ListView):
    """
    Dashboard view for Service Delivery team, focused on jobs ready for Provisioning,
    and jobs recently Provisioned.
    """
    model = SplicingJob
    template_name = 'splicing/sd_dashboard.html'
    context_object_name = 'jobs'
    ordering = ['-end_date']
    paginate_by = 20

    def test_func(self):
        # Checks if the user belongs to the Service Delivery group
        return is_service_delivery(self.request.user)

    def get_queryset(self):

        required_statuses = [
            self.model.SERVICE_DELIVERY_PENDING,
            self.model.JOB_PROVISIONED,
        ]

        print(f"DEBUG: SD Dashboard Querying for statuses: {required_statuses}")

        # Base Queryset: Jobs that require SD action (SERVICE_DELIVERY_PENDING) OR have recently been provisioned
        # CRITICAL: end_date__isnull=False ensures the job has been completed by the FE/Contractor
        queryset = self.model.objects.filter(
            status__in=required_statuses,
            end_date__isnull=False
        ).order_by('-end_date')

        # --- INTEGRATED FILTERING LOGIC ---
        filter_form = JobFilterForm(self.request.GET)

        if filter_form.is_valid():
            data = filter_form.cleaned_data

            # 1. Search Query (Job ID, Project Code, Customer Name, City)
            search_query = data.get('search_query')
            if search_query:
                queryset = queryset.filter(
                    Q(job_id__icontains=search_query) |
                    Q(project_code__icontains=search_query) |
                    Q(customer_name__icontains=search_query) |
                    Q(city__icontains=search_query)
                )

            # 2. Status Filter (Only within the allowed SD statuses)
            status = data.get('status')
            if status and status in required_statuses:
                queryset = queryset.filter(status=status)

            # 3. Assigned FE Filter (if the FE field is included in JobFilterForm)
            assigned_fe = data.get('assigned_fe')
            if assigned_fe:
                queryset = queryset.filter(assigned_fe=assigned_fe)
        # --- END INTEGRATED FILTERING LOGIC ---

        print(f"DEBUG: Final Filtered Queryset Count: {queryset.count()}")

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Calculate the number of jobs currently requiring action
        context['jobs_pending_provisioning'] = self.model.objects.filter(
            status=self.model.SERVICE_DELIVERY_PENDING
        ).count()

        # Count of jobs provisioned today
        context['jobs_provisioned_today'] = self.model.objects.filter(
            status=self.model.JOB_PROVISIONED,
            provisioning_record__configured_on__date=timezone.now().date()
        ).count()

        # Pass the filter form to the template for rendering
        context['filter_form'] = JobFilterForm(self.request.GET)

        return context

# ====================================================================
# UNIFIED PROVISIONING VIEW (Handles Create/Update/Detail)
# ====================================================================

class ProvisioningJobView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    """
    Handles creating, viewing, and updating the ProvisioningRecord for a SplicingJob.
    """
    model = ProvisioningRecord
    form_class = ProvisioningRecordForm
    template_name = 'splicing/provisioning_record_form.html'
    slug_field = 'splicing_job__job_id'
    slug_url_kwarg = 'job_id'

    def test_func(self):
        return is_service_delivery(self.request.user) or self.request.user.is_superuser

    def get_object(self, queryset=None):
        """
        Custom get_object logic: Returns existing ProvisioningRecord or None.
        """
        job_id = self.kwargs.get(self.slug_url_kwarg)
        try:
            return ProvisioningRecord.objects.get(**{self.slug_field: job_id})
        except ProvisioningRecord.DoesNotExist:
            return None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        job_id = self.kwargs.get(self.slug_url_kwarg)
        job = get_object_or_404(SplicingJob, job_id=job_id)
        context['job'] = job

        if self.object is None:
            # Creation flow: Pass job as initial value
            context['form'] = self.form_class(initial={'splicing_job': job})
            context['is_update'] = False
        else:
            context['is_update'] = True

        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()  # Existing record or None

        job_id = self.kwargs.get(self.slug_url_kwarg)
        job = get_object_or_404(SplicingJob, job_id=job_id)

        # --- 1. Handle Creation (If self.object is None) ---
        if self.object is None:
            form = self.form_class(request.POST)

            if form.is_valid():
                with transaction.atomic():
                    new_record = form.save(commit=False)
                    new_record.splicing_job = job
                    new_record.configured_by = self.request.user
                    new_record.configured_on = timezone.now()  # Set configured date/time
                    new_record.save()
                    self.object = new_record  # Set object for success URL

                    # FIX APPLIED: Transition Job Status to PROVISIONED upon creation.
                    if job.status != SplicingJob.JOB_PROVISIONED:
                        job.status = SplicingJob.JOB_PROVISIONED
                        job.save()
                        messages.success(request,
                                         f"Provisioning Record for Job {job.job_id} created. Status updated to Provisioned.")
                    else:
                        messages.success(request, f"Provisioning Record for Job {job.job_id} created.")

                return HttpResponseRedirect(self.get_success_url())
            else:
                # Re-render the context with the invalid form
                return self.render_to_response(self.get_context_data(form=form))

        # --- 2. Handle Update (If self.object exists) ---
        else:
            # Use standard UpdateView post logic, which calls form_valid
            return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        # Runs for Update mode only.
        if self.object:
            self.object.configured_by = self.request.user
            self.object.configured_on = timezone.now()

        messages.success(self.request,
                         f"Provisioning Record for Job {self.object.splicing_job.job_id} updated successfully.")
        return super().form_valid(form)

    def get_success_url(self):
        # Redirect back to the provisioning view, which now shows the update/detail form
        return reverse('provisioning_job', kwargs={'job_id': self.object.splicing_job.job_id})

class CompletedJobsAllView(LoginRequiredMixin, ListView):
    model = SplicingJob
    template_name = 'splicing/completed_jobs_dashboard.html'
    context_object_name = 'completed_jobs'

    def get_queryset(self):
        user = self.request.user
        queryset = SplicingJob.objects.filter(status=SplicingJob.JOB_PROVISIONED, end_date__isnull=False)

        if is_field_engineer(user) and not is_manager(user) and not is_contractor(user) and not is_service_delivery(
                user):
            queryset = queryset.filter(assigned_fe=user)

        elif is_contractor(user) and not is_service_delivery(user):
            try:
                contractor_company_name = user.profile.contractor_company_name
                if contractor_company_name:
                    queryset = queryset.filter(splicing_contractor_company__iexact=contractor_company_name)
                else:
                    queryset = queryset.none()
            except (UserProfile.DoesNotExist, AttributeError):
                queryset = queryset.none()

        return queryset.annotate(
            duration_field=ExpressionWrapper(
                F('end_date') - F('start_date'),
                output_field=DurationField()
            )
        ).order_by('-end_date')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        contractor_company_name = None

        # 1. Safely retrieve the contractor company name using the CORRECT related_name 'profile'
        if user.is_authenticated:
            try:
                contractor_company_name = user.profile.contractor_company_name
            except AttributeError:
                # Profile does not exist, company name remains None.
                pass

        # 2. Add the company name to the context (for the header: {{ contractor_company }})
        context['contractor_company'] = contractor_company_name

        # 3. Calculate all statistics
        if contractor_company_name:
            # Queryset containing ALL jobs assigned to this company (used for total/completed counts)
            all_contractor_jobs = self.model.objects.filter(
                splicing_contractor_company__iexact=contractor_company_name
            )

            # 3a. TOTAL JOBS
            context['jobs_total'] = all_contractor_jobs.count()

            # 3b. IN PROGRESS JOBS
            active_statuses = [self.model.FE_ASSIGNED, self.model.JOB_IN_PROGRESS]
            jobs_active_count = all_contractor_jobs.filter(status__in=active_statuses).count()
            context['jobs_in_progress'] = jobs_active_count

            # 3c. COMPLETED TODAY
            today = date.today()
            jobs_completed_today_count = all_contractor_jobs.filter(
                status=self.model.JOB_PROVISIONED,
                end_date__date=today
            ).count()

            context['jobs_completed_today'] = jobs_completed_today_count

        else:
            # Default all stats to 0 or None if the contractor profile is missing
            context['jobs_total'] = 0
            context['jobs_in_progress'] = 0
            context['jobs_completed_today'] = 0

        return context

class ContractorDashboardView(LoginRequiredMixin, UserPassesTestMixin, ListView):
    """Dashboard view for Contractors, filtered by their company name."""
    model = SplicingJob
    template_name = 'splicing/contractor_dashboard.html'
    context_object_name = 'jobs'
    ordering = ['priority', '-start_date']

    def test_func(self):
        # Checks if the user belongs to the 'Contractors' group
        return is_contractor(self.request.user)

    def get_contractor_company_name(self):
        user = self.request.user
        if user.is_authenticated:
            try:
                # Use the correct related_name 'profile'
                return user.profile.contractor_company_name
            except AttributeError:
                pass
        return None

    def get_queryset(self):
        contractor_company_name = self.get_contractor_company_name()
        if not contractor_company_name:
            return self.model.objects.none()

        # Base Queryset: Filter jobs assigned to this company
        queryset = self.model.objects.filter(splicing_contractor_company__iexact=contractor_company_name)

        # Exclude statuses that are truly irrelevant
        excluded_statuses = [
            self.model.JOB_DRAFT,
            self.model.JOB_CANCELLED,
            self.model.JOB_CLOSED_ARCHIVED,
        ]

        # The 'jobs' variable (the table list) now excludes only the highly inactive statuses.
        queryset = queryset.exclude(status__in=excluded_statuses).order_by('priority', '-start_date')

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        contractor_company_name = self.get_contractor_company_name()
        context['contractor_company'] = contractor_company_name

        # Default all stats to 0 or None if the contractor profile is missing
        if not contractor_company_name:
            context['jobs_total'] = 0
            context['jobs_in_progress'] = 0
            context['jobs_completed_today'] = 0
            return context

        # Queryset containing ALL jobs assigned to this company (used for ALL stats)
        all_contractor_jobs = self.model.objects.filter(
            splicing_contractor_company__iexact=contractor_company_name
        )

        # 3a. TOTAL ASSIGNED JOBS (The count that reflects the entire assigned history)
        context['jobs_total'] = all_contractor_jobs.count()

        # 3b. CURRENTLY IN PROGRESS (Statuses requiring active work)
        active_statuses = [
            self.model.FE_ASSIGNED,
            self.model.JOB_IN_PROGRESS
        ]
        jobs_active_count = all_contractor_jobs.filter(status__in=active_statuses).count()
        context['jobs_in_progress'] = jobs_active_count

        # 3c. COMPLETED TODAY
        today = date.today()

        # Use the actual constants from your model that signify contractor completion
        # CRITICAL FIX: Removed self.model.COMPLETED as it causes the AttributeError.
        completion_statuses = [
            self.model.SERVICE_DELIVERY_PENDING,
            self.model.JOB_PROVISIONED
        ]

        # Filter by completion statuses AND where the 'end_date' is today.
        jobs_completed_today_count = all_contractor_jobs.filter(
            status__in=completion_statuses,
            end_date__date=today
        ).count()

        context['jobs_completed_today'] = jobs_completed_today_count

        return context




# Assume is_contractor, SplicingJob, and ContractorStatusUpdateForm are imported

def clean_company_name(s):
    """
    Standardizes company names by removing hidden characters,
    excess whitespace, and case sensitivity.
    """
    if not s:
        return ''
    s = str(s).strip()
    # Removes non-breaking spaces (\xa0), zero-width spaces (\u200b),
    # and standardizes other common web-encoded spaces
    s = s.replace('\xa0', ' ').replace('\u200b', '').replace('\u00a0', ' ')
    # Collapses multiple spaces into one and lowercase
    return " ".join(s.split()).lower()

def is_contractor(user):
    """Checks if a user belongs to the 'Contractors' group."""
    return user.groups.filter(name='Contractors').exists()

# -----------------------------------------------------

class ContractorStatusUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = SplicingJob
    form_class = ContractorStatusUpdateForm
    template_name = 'splicing/contractor_status_update_form.html'
    slug_field = 'job_id'
    slug_url_kwarg = 'job_id'
    success_url = reverse_lazy('contractor_dashboard')

    def test_func(self):
        user = self.request.user
        if not user.groups.filter(name='Contractors').exists():
            return False
        try:
            job = self.get_object()
            if job.status in ['ARCHIVED', 'CLOSEOUT_COMPLETE', 'Job Closed and Archived']:
                return False

            def manual_clean(s):
                if not s: return ''
                s = str(s).strip().replace('\xa0', ' ').replace('\u200b', '').replace('\u00a0', ' ')
                return " ".join(s.split()).lower()

            user_company = manual_clean(getattr(user.profile, 'contractor_company_name', ''))
            job_company = manual_clean(job.splicing_contractor_company)
            return (user_company == job_company and user_company != '')
        except Exception as e:
            print(f"DEBUG: Critical Error in test_func: {e}")
            return False

    def post(self, request, *args, **kwargs):
        """
        Handles the 'COMPLETED' choice before the form tries to validate it
        against the Model's choices.
        """
        self.object = self.get_object()
        form = self.get_form()

        # Check the raw POST data for the 'COMPLETED' trigger
        if request.POST.get('status') == 'COMPLETED':
            job = self.object
            # Explicitly set the status to the constant your model actually accepts
            job.status = SplicingJob.SERVICE_DELIVERY_PENDING
            if not job.end_date:
                job.end_date = timezone.now()
            job.save()
            messages.success(self.request, f"Job {job.job_id} submitted to Service Delivery.")
            return redirect(self.get_success_url())

        return super().post(request, *args, **kwargs)


class JobMetadataUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = SplicingJob
    form_class = JobMetadataUpdateForm
    template_name = 'splicing/job_metadata_update.html'
    slug_field = 'job_id'
    slug_url_kwarg = 'job_id'

    def test_func(self):
        job = self.get_object()
        user = self.request.user
        # Ensure is_manager is defined in your helper functions
        return job.creator == user or user.groups.filter(name='Splicing_Managers').exists() or user.is_superuser

    def get_success_url(self):
        messages.success(self.request, f"Job {self.object.job_id} metadata updated successfully.")
        return reverse('job_detail', kwargs={'job_id': self.object.job_id})


class FEStatusUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = SplicingJob
    form_class = FEStatusUpdateForm
    template_name = 'splicing/fe_job_update.html'
    slug_field = 'job_id'
    slug_url_kwarg = 'job_id'

    def test_func(self):
        job = self.get_object()
        user = self.request.user
        return (user.groups.filter(name='Field_Engineers').exists()
                and job.assigned_fe == user
                and job.status != SplicingJob.JOB_PROVISIONED)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        job = self.object
        context['status_form'] = context.get('form') or FEStatusUpdateForm(instance=job)
        context['can_edit'] = self.test_func()
        context['job'] = job
        context['metadata_update_url'] = reverse('job_update_metadata', kwargs={'job_id': job.job_id})
        return context

    def form_valid(self, form):
        job = form.instance
        new_status = form.cleaned_data.get('status')

        if new_status == SplicingJob.SERVICE_DELIVERY_PENDING:
            response = super().form_valid(form)
            if not job.end_date:
                job.end_date = timezone.now()
                job.save(update_fields=['end_date'])
            messages.success(self.request, f"Job {job.job_id} marked complete.")
            return response
        else:
            messages.success(self.request, f"Job {job.job_id} status updated.")
            return super().form_valid(form)

    def get_success_url(self):
        return reverse('job_detail', kwargs={'job_id': self.object.job_id})
# --- AJAX VIEW ---

@login_required
def load_switches(request):
    """
    AJAX endpoint to load PopLocation details (Switches) based on the selected PopLocation.
    """
    pop_id = request.GET.get('pop_id')
    switches = Switch.objects.filter(pop_location_id=pop_id).order_by('name')
    return render(request, 'splicing/pop_switch_dropdown_list.html', {'switches': switches})


# ====================================================================
# 4. ADVANCED REPORTING & EXPORTS
# ====================================================================




class AdvancedReportView(LoginRequiredMixin, TemplateView):
    template_name = 'splicing/advanced_report.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        now = timezone.now()

        # 1. BASE DATA & FILTERING
        base_qs = SplicingJob.objects.all()
        filtered_qs = base_qs.select_related('assigned_fe')

        search_query = self.request.GET.get('job_id')
        status_filter = self.request.GET.get('status')
        fe_filter = self.request.GET.get('assigned_fe')

        if search_query:
            filtered_qs = filtered_qs.filter(
                Q(job_id__icontains=search_query) |
                Q(customer_name__icontains=search_query)
            )
        if status_filter:
            filtered_qs = filtered_qs.filter(status=status_filter)
        if fe_filter:
            filtered_qs = filtered_qs.filter(assigned_fe_id=fe_filter)

        # 2. GLOBAL METRICS (Top Ribbon)
        context['metrics'] = {
            'completed_today': base_qs.filter(end_date__date=now.date()).count(),
            'completed_wtd': base_qs.filter(end_date__gte=now - timedelta(days=7)).count(),
            'completed_mtd': base_qs.filter(end_date__month=now.month, end_date__year=now.year).count(),
            'completed_ytd': base_qs.filter(
                status__in=['PROVISIONED', 'CLOSED_ARCHIVED'],
                end_date__year=now.year
            ).count(),
            'pending_total': base_qs.filter(status='SD_PENDING').count(),
            'in_progress_total': base_qs.filter(status='IN_PROGRESS').count(),
        }

        # 3. ASSIGNMENT DATA (Unified Workload Table)
        # This standardizes the keys to prevent "VariableDoesNotExist" errors
        assignment_data = []

        # A. Internal Staff
        staff_stats = User.objects.filter(fe_jobs__in=filtered_qs).annotate(
            job_count=Count('fe_jobs', filter=Q(fe_jobs__in=filtered_qs))
        )
        for user in staff_stats:
            assignment_data.append({
                'display_name': user.get_full_name() or user.username,
                'assigned_count': user.job_count,
                'is_contractor': False
            })

        # B. Contractors
        contractor_stats = filtered_qs.exclude(
            Q(splicing_contractor_company__isnull=True) | Q(splicing_contractor_company='')
        ).values('splicing_contractor_company').annotate(
            job_count=Count('id')
        )
        for con in contractor_stats:
            assignment_data.append({
                'display_name': con['splicing_contractor_company'],
                'assigned_count': con['job_count'],
                'is_contractor': True
            })

        context['assignment_data'] = sorted(assignment_data, key=lambda x: x['assigned_count'], reverse=True)

        # 4. CHART DATA
        status_dist = filtered_qs.values('status').annotate(count=Count('id'))
        context['status_distribution'] = [
            {'status': item['status'], 'count': item['count']} for item in status_dist
        ]

        done = filtered_qs.filter(status__in=['PROVISIONED', 'CLOSED_ARCHIVED']).count()
        active = filtered_qs.exclude(status__in=['PROVISIONED', 'CLOSED_ARCHIVED']).count()
        context['completion_breakdown'] = [
            {'label': 'Completed', 'count': done},
            {'label': 'Active/Pending', 'count': active}
        ]

        # Monthly Trend
        rec_trend = filtered_qs.annotate(m=TruncMonth('start_date')).values('m').annotate(c=Count('id'))
        comp_trend = filtered_qs.filter(
            status__in=['PROVISIONED', 'CLOSED_ARCHIVED'],
            end_date__isnull=False
        ).annotate(m=TruncMonth('end_date')).values('m').annotate(c=Count('id'))

        trend_map = {}
        for x in rec_trend:
            if x['m']:
                lbl = x['m'].strftime('%b %Y')
                trend_map[lbl] = {'month': lbl, 'received': x['c'], 'completed': 0, 'sort': x['m']}

        for x in comp_trend:
            if x['m']:
                lbl = x['m'].strftime('%b %Y')
                if lbl in trend_map:
                    trend_map[lbl]['completed'] = x['c']
                else:
                    trend_map[lbl] = {'month': lbl, 'received': 0, 'completed': x['c'], 'sort': x['m']}

        context['completion_trend'] = sorted(trend_map.values(), key=lambda x: x['sort'])

        # 5. FINAL WRAP UP
        context['job_list'] = filtered_qs.order_by('-start_date')[:100]
        context['filter_form'] = JobFilterForm(self.request.GET)
        context['SplicingJob_STATUS_CHOICES'] = SplicingJob.STATUS_CHOICES

        return context


@login_required
@user_passes_test(is_advanced_reporter)
def export_advanced_report_excel(request):
    """
    Generates an Excel export of the Splicing Jobs.
    """
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="Splicing_Report_{timezone.now().date()}.xlsx"'

    wb = Workbook()
    ws = wb.active
    ws.title = "Splicing Jobs Report"

    # Header Row
    headers = [
        'Job ID', 'Project Code', 'Customer', 'City',
        'Status', 'Priority', 'Assigned FE', 'Start Date', 'End Date'
    ]
    ws.append(headers)

    # Data Rows
    jobs = SplicingJob.objects.all().values_list(
        'job_id', 'project_code', 'customer_name', 'city',
        'status', 'priority', 'assigned_fe__username', 'start_date', 'end_date'
    )

    for job in jobs:
        # Convert datetimes to naive for Excel compatibility if necessary
        row = list(job)
        ws.append(row)

    wb.save(response)
    return response


class JobCloseoutView(LoginRequiredMixin, ManagerRequiredMixin, UpdateView):
    """
    View for Managers to mark a job as closed and archived.
    """
    model = SplicingJob
    form_class = JobCloseoutForm
    template_name = 'splicing/job_closeout.html'
    slug_field = 'job_id'
    slug_url_kwarg = 'job_id'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['job'] = self.object
        return context

    def form_valid(self, form):
        # 1. Update status to Closed/Archived
        form.instance.status = SplicingJob.JOB_CLOSED_ARCHIVED

        # 2. Set end_date if it wasn't set by FE or is the final closeout date
        if not form.instance.end_date:
            form.instance.end_date = timezone.now()

        messages.success(self.request,
                         f"Job {self.object.job_id} closed out and archived. It is now pending Service Delivery provisioning.")
        return super().form_valid(form)

    def get_success_url(self):
        # Redirect to the main job detail page
        return reverse('job_detail', kwargs={'job_id': self.object.job_id})


# --- REPORT EXPORT FUNCTIONS ---

@login_required
@user_passes_test(is_advanced_reporter)
def export_advanced_report_excel(request):
    """Generates and returns the Advanced Report data as an Excel file."""
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response[
        'Content-Disposition'] = f'attachment; filename="advanced_report_{timezone.now().strftime("%Y%m%d_%H%M%S")}.xlsx"'

    # Placeholder for actual Excel generation logic (Workbook setup, sheet creation, data population)
    wb = Workbook()
    ws = wb.active
    ws.title = "Advanced Report"

    # Simplified structure: Just job IDs and Status
    ws.append(['Job ID', 'Project Code', 'Status', 'Start Date'])

    jobs = SplicingJob.objects.all().order_by('-start_date')
    for job in jobs[:1000]:  # Limit for safety
        ws.append([
            job.job_id,
            job.project_code,
            job.get_status_display(),
            job.start_date.strftime('%Y-%m-%d') if job.start_date else '',
        ])

    wb.save(response)
    return response


@login_required
@user_passes_test(is_advanced_reporter)
def export_advanced_report_pdf(request):
    """Generates and returns the Advanced Report data as a PDF file."""

    # 1. Gather data and rename the variable to match the template's expectation (job_list)
    job_list = SplicingJob.objects.all().order_by('-start_date')[:50]

    # 2. Gather context needed by the template
    context = {
        # CRITICAL: Pass job_list to match the template's 'for' loop
        'job_list': job_list,

        # CRITICAL: Pass the 'date' variable
        'date': timezone.now().strftime("%Y-%m-%d"),

        # Pass request for filter access, though reading request.GET inside the template is better
        'request': request,

        # The template attempts to read status_label, we'll try to calculate a basic one
        'status_label': 'All Jobs (Top 50)',

        # If the template needs access to the SplicingJob class (e.g., for constants), pass it
        'SplicingJob': SplicingJob,
    }

    # 3. Render HTML template with data
    html_string = render_to_string('splicing/pdf/advanced_report_pdf.html', context)

    # 4. Generate PDF
    html = HTML(string=html_string)
    pdf_file = html.write_pdf()

    # 5. Create HTTP response
    response = HttpResponse(pdf_file, content_type='application/pdf')
    response[
        'Content-Disposition'] = f'inline; filename="advanced_report_{timezone.now().strftime("%Y%m%d_%H%M%S")}.pdf"'

    return response