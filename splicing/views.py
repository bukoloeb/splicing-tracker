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
# 2. PERMISSION MIXINS (CRITICAL: Defined before views use them)
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
# 3. SECURITY & REDIRECT VIEWS
# ====================================================================

@login_required
def custom_dashboard_redirect(request):
    """Gatekeeper for first-time password changes and role-based entry."""
    user = request.user
    try:
        if hasattr(user, 'profile') and user.profile.must_change_password:
            messages.warning(request, "Security Policy: Please change your password before continuing.")
            return redirect('password_change')
    except Exception:
        pass

    if is_manager(user): return redirect('job_dashboard')
    if is_service_delivery(user): return redirect('sd_dashboard')
    if is_technical_manager(user): return redirect('tech_manager_report')
    if is_contractor(user): return redirect('contractor_dashboard')

    return redirect('job_dashboard')


class MyPasswordChangeView(auth_views.PasswordChangeView):
    """Clears 'must_change_password' flag upon success."""
    success_url = reverse_lazy('password_change_done')
    template_name = 'registration/password_change_form.html'

    def form_valid(self, form):
        response = super().form_valid(form)
        if hasattr(self.request.user, 'profile'):
            self.request.user.profile.must_change_password = False
            self.request.user.profile.save()
        return response


# ====================================================================
# 4. CORE JOB WORKFLOW VIEWS
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

        # Multi-Tenant Data Isolation
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
                    Q(job_id__icontains=q) | Q(customer_name__icontains=q) | Q(circuit_id__icontains=q))
            if data.get('status'):
                queryset = queryset.filter(status=data.get('status'))

        return queryset.annotate(
            job_age=ExpressionWrapper(timezone.now() - F('start_date'), output_field=DurationField()))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
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
        """Strict isolation to prevent cross-contractor data leakage."""
        user = self.request.user
        job = self.get_object()
        if user.is_superuser or is_manager(user) or is_service_delivery(user): return True
        if is_contractor(user):
            try:
                return job.splicing_contractor_company.lower() == user.profile.contractor_company_name.lower()
            except Exception:
                return False
        if is_field_engineer(user): return job.assigned_fe == user
        return is_job_viewer(user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user_groups = set(self.request.user.groups.values_list('name', flat=True))

        def check_role(role_name):
            return self.request.user.is_superuser or (role_name in user_groups)

        context.update({
            'is_manager': check_role('Splicing_Managers'),
            'is_fe': check_role('Field_Engineers'),
            'is_contractor': check_role('Contractors'),
            'is_service_delivery': check_role('Service_Delivery'),
        })
        try:
            context['provisioning_record'] = self.object.provisioning_record
        except Exception:
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
        """Harden file and port number saving."""
        self.object = self.get_object()
        form = self.form_class(request.POST, request.FILES, instance=self.object)
        if form.is_valid():
            job = form.save(commit=False)
            if form.cleaned_data.get('status') == SplicingJob.SERVICE_DELIVERY_PENDING:
                if not job.end_date: job.end_date = timezone.now()
            job.save()
            messages.success(request, "Engineering data committed successfully.")
            return HttpResponseRedirect(self.get_success_url())
        return self.render_to_response(self.get_context_data(form=form))

    def get_success_url(self):
        return reverse('job_detail', kwargs={'job_id': self.object.job_id})


# ====================================================================
# 5. REMAINING DASHBOARDS & UTILITIES
# ====================================================================

class ContractorDashboardView(LoginRequiredMixin, UserPassesTestMixin, ListView):
    model = SplicingJob
    template_name = 'splicing/contractor_dashboard.html'
    context_object_name = 'jobs'

    def test_func(self):
        return is_contractor(self.request.user)

    def get_queryset(self):
        try:
            company = self.request.user.profile.contractor_company_name
            return SplicingJob.objects.filter(splicing_contractor_company__iexact=company).exclude(
                status__in=['JOB_DRAFT', 'CANCELLED']).order_by('priority', '-start_date')
        except AttributeError:
            return SplicingJob.objects.none()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        company = getattr(self.request.user.profile, 'contractor_company_name', None)
        if company:
            qs = SplicingJob.objects.filter(splicing_contractor_company__iexact=company)
            context.update({'contractor_company': company, 'jobs_total': qs.count(),
                            'jobs_in_progress': qs.filter(status__in=['FE_ASSIGNED', 'IN_PROGRESS']).count(),
                            'jobs_completed_today': qs.filter(status__in=['SD_PENDING', 'PROVISIONED'],
                                                              end_date__date=date.today()).count()})
        return context


class ServiceDeliveryDashboardView(LoginRequiredMixin, UserPassesTestMixin, ListView):
    model = SplicingJob
    template_name = 'splicing/sd_dashboard.html'
    context_object_name = 'jobs'

    def test_func(self): return is_service_delivery(self.request.user)

    def get_queryset(self):
        queryset = SplicingJob.objects.filter(
            status__in=[SplicingJob.SERVICE_DELIVERY_PENDING, SplicingJob.JOB_PROVISIONED],
            end_date__isnull=False).order_by('-end_date')
        search = self.request.GET.get('search_query')
        if search:
            queryset = queryset.filter(Q(job_id__icontains=search) | Q(project_code__icontains=search) | Q(
                customer_name__icontains=search) | Q(circuit_id__icontains=search))
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({'jobs_pending_provisioning': SplicingJob.objects.filter(
            status=SplicingJob.SERVICE_DELIVERY_PENDING).count(),
                        'jobs_provisioned_today': SplicingJob.objects.filter(status=SplicingJob.JOB_PROVISIONED,
                                                                             provisioning_record__configured_on__date=timezone.now().date()).count(),
                        'filter_form': JobFilterForm(self.request.GET)})
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
        try:
            return ProvisioningRecord.objects.get(splicing_job__job_id=self.kwargs.get('job_id'))
        except Exception:
            return None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        job = get_object_or_404(SplicingJob, job_id=self.kwargs.get('job_id'))
        context['job'] = job
        if not self.object: context['form'] = self.form_class(initial={'splicing_job': job})
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        if not self.object:
            job = get_object_or_404(SplicingJob, job_id=self.kwargs.get('job_id'))
            form = self.form_class(request.POST)
            if form.is_valid():
                with transaction.atomic():
                    rec = form.save(commit=False)
                    rec.splicing_job, rec.configured_by, rec.configured_on = job, request.user, timezone.now()
                    rec.save()
                    job.status = SplicingJob.JOB_PROVISIONED
                    job.save()
                return HttpResponseRedirect(reverse('provisioning_job', kwargs={'job_id': job.job_id}))
        return super().post(request, *args, **kwargs)


class JobCloseoutView(LoginRequiredMixin, ManagerRequiredMixin, UpdateView):
    model = SplicingJob
    form_class = JobCloseoutForm
    template_name = 'splicing/job_closeout.html'
    slug_field = 'job_id'
    slug_url_kwarg = 'job_id'

    def form_valid(self, form):
        form.instance.status = SplicingJob.JOB_CLOSED_ARCHIVED
        if not form.instance.end_date: form.instance.end_date = timezone.now()
        messages.success(self.request, "Job archived.")
        return super().form_valid(form)

    def get_success_url(self): return reverse('job_detail', kwargs={'job_id': self.object.job_id})


class JobAssignmentView(LoginRequiredMixin, ManagerRequiredMixin, UpdateView):
    model = SplicingJob
    form_class = JobAssignmentForm
    template_name = 'splicing/job_assign.html'
    slug_field = 'job_id'
    slug_url_kwarg = 'job_id'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.instance.status = SplicingJob.FE_ASSIGNED
        return super().form_valid(form)

    def get_success_url(self): return reverse('job_detail', kwargs={'job_id': self.object.job_id})


class JobCreateView(LoginRequiredMixin, JobCreatorRequiredMixin, CreateView):
    model = SplicingJob
    form_class = SplicingJobCreationForm
    template_name = 'splicing/job_create.html'

    def form_valid(self, form):
        form.instance.creator = self.request.user
        form.instance.status = SplicingJob.MANAGER_ASSIGNED
        form.instance.job_id = generate_job_id()
        return super().form_valid(form)

    def get_success_url(self): return reverse('job_detail', kwargs={'job_id': self.object.job_id})


class JobMetadataUpdateView(LoginRequiredMixin, UpdateView):
    model = SplicingJob
    form_class = JobMetadataUpdateForm
    template_name = 'splicing/job_metadata_update.html'
    slug_field = 'job_id'
    slug_url_kwarg = 'job_id'

    def get_success_url(self): return reverse('job_detail', kwargs={'job_id': self.object.job_id})


@login_required
def load_switches(request):
    switches = Switch.objects.filter(pop_location_id=request.GET.get('pop_id')).order_by('name')
    return render(request, 'splicing/pop_switch_dropdown_list.html', {'switches': switches})


class JobViewerView(LoginRequiredMixin, ListView):
    model = SplicingJob
    template_name = 'splicing/job_viewer_dashboard.html'
    context_object_name = 'jobs'

    def get_queryset(self): return SplicingJob.objects.exclude(status__in=['CLOSED_ARCHIVED', 'PROVISIONED']).order_by(
        '-start_date')


class TechnicalManagerReportView(LoginRequiredMixin, TemplateView):
    template_name = 'splicing/technical_manager_report.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        jobs_qs = SplicingJob.objects.all().select_related('assigned_fe')
        context['jobs_list'] = jobs_qs.order_by('-start_date')[:100]
        context['filter_form'] = JobFilterForm(self.request.GET)
        return context