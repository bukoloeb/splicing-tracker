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
from django.contrib.auth import views as auth_views
import uuid
import pytz
from datetime import timedelta, date
from weasyprint import HTML
from openpyxl import Workbook

# --- 1. Model Imports ---
from .models import (
    SplicingJob,
    PopLocation,
    Switch,
    UserProfile,
    ProvisioningRecord
)

# --- 2. Application Form Imports ---
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
# 1. HELPER FUNCTIONS & ROLE CHECKS
# ====================================================================

def generate_job_id():
    """Generates a unique, short Job ID."""
    return str(uuid.uuid4())[:8].upper()


def is_manager(user):
    return user.groups.filter(name='Splicing_Managers').exists() or user.is_superuser


def is_technical_manager(user):
    return user.groups.filter(name='Technical_Managers').exists() or user.is_superuser


def is_field_engineer(user):
    return user.groups.filter(name='Field_Engineers').exists()


def is_job_creator(user):
    return user.groups.filter(name='Job_Creators').exists()


def is_job_viewer(user):
    return user.groups.filter(name='Viewer').exists() or user.is_superuser


def is_contractor(user):
    return user.groups.filter(name='Contractors').exists()


def is_service_delivery(user):
    return user.groups.filter(name='Service_Delivery').exists() or user.is_superuser


def is_advanced_reporter(user):
    return user.has_perm('splicing.can_view_advanced_report') or user.is_superuser


# ====================================================================
# 2. SECURITY & REDIRECT VIEWS
# ====================================================================

@login_required
def custom_dashboard_redirect(request):
    """
    Gatekeeper: Enforces Password Policy and redirects users to their specific hubs.
    """
    user = request.user

    # SECURITY GATE: Force Password Change on First Login
    try:
        if hasattr(user, 'profile') and user.profile.must_change_password:
            messages.warning(request, "Security Policy: You must change your password before accessing the tracker.")
            return redirect('password_change')
    except Exception:
        pass

    if is_manager(user):
        return redirect('job_dashboard')
    if is_service_delivery(user):
        return redirect('sd_dashboard')
    if is_technical_manager(user):
        return redirect('tech_manager_report')
    if is_contractor(user):
        return redirect('contractor_dashboard')
    if is_field_engineer(user) or is_job_creator(user):
        return redirect('job_dashboard')

    return redirect('job_dashboard')


class MyPasswordChangeView(auth_views.PasswordChangeView):
    """
    Custom Password Change View that clears the 'must_change_password' flag
    upon successful completion.
    """
    success_url = reverse_lazy('password_change_done')
    template_name = 'registration/password_change_form.html'

    def form_valid(self, form):
        response = super().form_valid(form)
        user = self.request.user
        if hasattr(user, 'profile'):
            user.profile.must_change_password = False
            user.profile.save()
            messages.success(self.request, "Security flag cleared. You now have full access.")
        return response


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

        # ROLE-BASED DATA ISOLATION
        if is_field_engineer(user) and not (is_manager(user) or is_technical_manager(user)):
            queryset = queryset.filter(assigned_fe=user)
        elif is_contractor(user) and not is_service_delivery(user):
            try:
                company = user.profile.contractor_company_name
                queryset = queryset.filter(splicing_contractor_company__iexact=company) if company else queryset.none()
            except AttributeError:
                queryset = queryset.none()

        if filter_form.is_valid():
            data = filter_form.cleaned_data
            if data.get('search_query'):
                q = data.get('search_query')
                queryset = queryset.filter(
                    Q(job_id__icontains=q) | Q(customer_name__icontains=q) | Q(circuit_id__icontains=q)
                )
            if data.get('status'):
                queryset = queryset.filter(status=data.get('status'))
            if data.get('assigned_fe'):
                queryset = queryset.filter(assigned_fe=data.get('assigned_fe'))

        return queryset.annotate(
            job_age=ExpressionWrapper(timezone.now() - F('start_date'), output_field=DurationField())
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        now = timezone.now()
        stats_qs = SplicingJob.objects.all()

        context.update({
            'pending_assignment': stats_qs.filter(status='PENDING_ASSIGNMENT').count(),
            'active_splicing': stats_qs.filter(status__in=['FE_ASSIGNED', 'IN_PROGRESS']).count(),
            'pending_provisioning': stats_qs.filter(status__in=['SD_PENDING']).count(),
            'filter_form': JobFilterForm(self.request.GET)
        })
        return context


class JobDetailView(LoginRequiredMixin, UserPassesTestMixin, DetailView):
    model = SplicingJob
    context_object_name = 'job'
    template_name = 'splicing/job_detail.html'
    slug_field = 'job_id'
    slug_url_kwarg = 'job_id'

    def test_func(self):
        """ STRICT MULTI-TENANT ISOLATION CONTROL """
        user = self.request.user
        job = self.get_object()

        if user.is_superuser or is_manager(user) or is_service_delivery(user) or is_technical_manager(user):
            return True
        if is_contractor(user):
            try:
                user_company = user.profile.contractor_company_name
                return job.splicing_contractor_company.lower() == user_company.lower() if user_company else False
            except AttributeError:
                return False
        if is_field_engineer(user):
            return job.assigned_fe == user
        if is_job_creator(user):
            return job.creator == user
        return is_job_viewer(user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        user_groups = set(user.groups.values_list('name', flat=True))

        def check_role(role_name):
            return user.is_superuser or (role_name in user_groups)

        context.update({
            'is_manager': check_role('Splicing_Managers'),
            'is_fe': check_role('Field_Engineers'),
            'is_contractor': check_role('Contractors'),
            'is_service_delivery': check_role('Service_Delivery'),
        })
        try:
            context['provisioning_record'] = self.object.provisioning_record
        except ProvisioningRecord.DoesNotExist:
            context['provisioning_record'] = None
        return context


class FEStatusUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = SplicingJob
    form_class = FEStatusUpdateForm
    template_name = 'splicing/fe_job_update.html'
    slug_field = 'job_id'
    slug_url_kwarg = 'job_id'

    def test_func(self):
        job = self.get_object()
        return (is_field_engineer(
            self.request.user) and job.assigned_fe == self.request.user and job.status != SplicingJob.JOB_PROVISIONED)

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        form = self.form_class(request.POST, request.FILES, instance=self.object)
        if form.is_valid():
            # Explicitly capture port number and files
            job = form.save(commit=False)
            if form.cleaned_data.get('status') == SplicingJob.SERVICE_DELIVERY_PENDING:
                if not job.end_date: job.end_date = timezone.now()
            job.save()
            messages.success(request, f"Progress for {job.job_id} committed successfully.")
            return HttpResponseRedirect(self.get_success_url())
        return self.render_to_response(self.get_context_data(form=form))

    def get_success_url(self):
        return reverse('job_detail', kwargs={'job_id': self.object.job_id})


class ContractorDashboardView(LoginRequiredMixin, UserPassesTestMixin, ListView):
    model = SplicingJob
    template_name = 'splicing/contractor_dashboard.html'
    context_object_name = 'jobs'

    def test_func(self):
        return is_contractor(self.request.user)

    def get_queryset(self):
        try:
            company = self.request.user.profile.contractor_company_name
            if not company: return SplicingJob.objects.none()
            return SplicingJob.objects.filter(splicing_contractor_company__iexact=company).exclude(
                status__in=['JOB_DRAFT', 'CANCELLED']).order_by('priority', '-start_date')
        except AttributeError:
            return SplicingJob.objects.none()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        company = getattr(self.request.user.profile, 'contractor_company_name', None)
        context['contractor_company'] = company
        if company:
            qs = SplicingJob.objects.filter(splicing_contractor_company__iexact=company)
            context.update({
                'jobs_total': qs.count(),
                'jobs_in_progress': qs.filter(status__in=['FE_ASSIGNED', 'IN_PROGRESS']).count(),
                'jobs_completed_today': qs.filter(status__in=['SD_PENDING', 'PROVISIONED'],
                                                  end_date__date=date.today()).count()
            })
        return context


# ====================================================================
# 4. SERVICE DELIVERY & PROVISIONING VIEWS
# ====================================================================

class ServiceDeliveryDashboardView(LoginRequiredMixin, UserPassesTestMixin, ListView):
    model = SplicingJob
    template_name = 'splicing/sd_dashboard.html'
    context_object_name = 'jobs'
    ordering = ['-end_date']
    paginate_by = 20

    def test_func(self):
        return is_service_delivery(self.request.user)

    def get_queryset(self):
        required_statuses = [SplicingJob.SERVICE_DELIVERY_PENDING, SplicingJob.JOB_PROVISIONED]
        queryset = SplicingJob.objects.filter(status__in=required_statuses, end_date__isnull=False).order_by(
            '-end_date')
        filter_form = JobFilterForm(self.request.GET)
        if filter_form.is_valid():
            data = filter_form.cleaned_data
            search = data.get('search_query')
            if search:
                queryset = queryset.filter(Q(job_id__icontains=search) | Q(project_code__icontains=search) | Q(
                    customer_name__icontains=search) | Q(circuit_id__icontains=search))
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'jobs_pending_provisioning': SplicingJob.objects.filter(
                status=SplicingJob.SERVICE_DELIVERY_PENDING).count(),
            'jobs_provisioned_today': SplicingJob.objects.filter(status=SplicingJob.JOB_PROVISIONED,
                                                                 provisioning_record__configured_on__date=timezone.now().date()).count(),
            'filter_form': JobFilterForm(self.request.GET)
        })
        return context


class ProvisioningJobView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = ProvisioningRecord
    form_class = ProvisioningRecordForm
    template_name = 'splicing/provisioning_record_form.html'
    slug_field = 'splicing_job__job_id'
    slug_url_kwarg = 'job_id'

    def test_func(self):
        return is_service_delivery(self.request.user) or self.request.user.is_superuser

    def get_object(self, queryset=None):
        job_id = self.kwargs.get(self.slug_url_kwarg)
        try:
            return ProvisioningRecord.objects.get(splicing_job__job_id=job_id)
        except ProvisioningRecord.DoesNotExist:
            return None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        job = get_object_or_404(SplicingJob, job_id=self.kwargs.get('job_id'))
        context['job'] = job
        if self.object is None:
            context['form'] = self.form_class(initial={'splicing_job': job})
            context['is_update'] = False
        else:
            context['is_update'] = True
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        job = get_object_or_404(SplicingJob, job_id=self.kwargs.get('job_id'))
        if self.object is None:
            form = self.form_class(request.POST)
            if form.is_valid():
                with transaction.atomic():
                    new_record = form.save(commit=False)
                    new_record.splicing_job = job
                    new_record.configured_by = self.request.user
                    new_record.configured_on = timezone.now()
                    new_record.save()
                    job.status = SplicingJob.JOB_PROVISIONED
                    job.save()
                return HttpResponseRedirect(self.get_success_url())
            return self.render_to_response(self.get_context_data(form=form))
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        if self.object:
            self.object.configured_by = self.request.user
            self.object.configured_on = timezone.now()
        messages.success(self.request, f"Provisioning updated for {self.object.splicing_job.job_id}")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('provisioning_job', kwargs={'job_id': self.kwargs.get('job_id')})


# ====================================================================
# 5. REMAINING REPORTS & UTILITIES
# ====================================================================

class TechnicalManagerReportView(LoginRequiredMixin, TemplateView):
    template_name = 'splicing/technical_manager_report.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        now = timezone.now()
        jobs_qs = SplicingJob.objects.all().select_related('assigned_fe')
        form = JobFilterForm(self.request.GET)
        if form.is_valid():
            search = form.cleaned_data.get('search_query')
            if search: queryset = jobs_qs.filter(Q(job_id__icontains=search) | Q(customer_name__icontains=search))

        display_list = list(jobs_qs.order_by('-start_date')[:100])
        for job in display_list:
            if job.start_date:
                end_point = job.end_date if job.end_date else now
                job.age_days = (end_point - job.start_date).days
            else:
                job.age_days = 0

        context.update({'jobs_list': display_list, 'filter_form': form})
        return context


@login_required
def load_switches(request):
    pop_id = request.GET.get('pop_id')
    switches = Switch.objects.filter(pop_location_id=pop_id).order_by('name')
    return render(request, 'splicing/pop_switch_dropdown_list.html', {'switches': switches})


class JobCloseoutView(LoginRequiredMixin, ManagerRequiredMixin, UpdateView):
    model = SplicingJob
    form_class = JobCloseoutForm
    template_name = 'splicing/job_closeout.html'
    slug_field = 'job_id'
    slug_url_kwarg = 'job_id'

    def form_valid(self, form):
        form.instance.status = SplicingJob.JOB_CLOSED_ARCHIVED
        if not form.instance.end_date: form.instance.end_date = timezone.now()
        messages.success(self.request, f"Job {self.object.job_id} archived.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('job_detail', kwargs={'job_id': self.object.job_id})