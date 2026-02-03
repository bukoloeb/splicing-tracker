from django.urls import path
from .views import (
    # Core Job Workflow
    DashboardView,
    JobCreateView,
    JobDetailView,
    JobMetadataUpdateView,
    JobAssignmentView,
    FEStatusUpdateView,
    JobCloseoutView,
    CompletedJobsAllView,

    # Detail Management / Lookups
    load_switches,

    # Role-Specific Dashboards & Views
    JobViewerView,
    TechnicalManagerReportView,
    ContractorDashboardView,
    AdvancedReportView,
    ContractorStatusUpdateView,  # <--- NEW IMPORT ADDED HERE

    # --- NEW EXPORT VIEW IMPORTS ---
    export_advanced_report_excel,
    export_advanced_report_pdf,

    # Service Delivery (Unified View)
    ServiceDeliveryDashboardView,
    ProvisioningJobView,

    # Redirect Helper
    custom_dashboard_redirect,
)

urlpatterns = [
    # 1. --- CORE REDIRECT ROUTE (Handles the root '/') ---
    path('', custom_dashboard_redirect, name='dashboard_redirect'),

    # --- CUSTOM REDIRECT VIEW (Keeping original definition for flexibility) ---
    path('dashboard/select/', custom_dashboard_redirect, name='custom_dashboard_redirect'),

    # 2. --- DASHBOARD & LIST VIEWS ---
    path('manager-dashboard/', DashboardView.as_view(), name='job_dashboard'),
    path('jobs/completed/all/', CompletedJobsAllView.as_view(), name='completed_jobs_all'),

    # --- CONTRACTOR SPECIFIC VIEWS ---
    path('contractor/dashboard/', ContractorDashboardView.as_view(), name='contractor_dashboard'),
    # === CRITICAL FIX: URL for the status update that was missing ===
    path(
        'contractor/jobs/<str:job_id>/update/',
        ContractorStatusUpdateView.as_view(), # <-- Must be imported above
        name='contractor_status_update' # <--- This fixes the NoReverseMatch error!
    ),

    # --- SERVICE DELIVERY VIEWS ---
    path('sd-dashboard/', ServiceDeliveryDashboardView.as_view(), name='sd_dashboard'),
    path('provision/<str:job_id>/', ProvisioningJobView.as_view(), name='provisioning_job'),

    # 3. --- NEW REPORTING VIEWS ---
    path('tech-reports/', TechnicalManagerReportView.as_view(), name='tech_manager_report'),
    path('advanced-reports/', AdvancedReportView.as_view(), name='advanced_report'),

    # --- EXPORT ENDPOINTS ---
    path('export-excel/', export_advanced_report_excel, name='export_excel'),
    path('export-pdf/', export_advanced_report_pdf, name='export_pdf'),

    # 4. JOB VIEWER STATUS CHECK
    path('status-check/', JobViewerView.as_view(), name='job_viewer_dashboard'),

    # --- JOB MANAGEMENT (CRUD) ---
    path('jobs/create/', JobCreateView.as_view(), name='job_create'),
    path('jobs/<str:job_id>/detail/', JobDetailView.as_view(), name='job_detail'),

    # --- JOB UPDATE/ASSIGNMENT WORKFLOW (Metadata, FE Status, Closeout) ---
    path('jobs/<str:job_id>/update/', JobMetadataUpdateView.as_view(), name='job_update_metadata'),
    path('jobs/<str:job_id>/assign/', JobAssignmentView.as_view(), name='job_assign'),
    path('jobs/<str:job_id>/status/', FEStatusUpdateView.as_view(), name='job_fe_status'),
    path('jobs/<str:job_id>/close/', JobCloseoutView.as_view(), name='job_closeout'),

    # --- AJAX ENDPOINTS ---
    path('ajax/load-switches/', load_switches, name='ajax_load_switches'),
]