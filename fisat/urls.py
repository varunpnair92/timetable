from django.urls import path,include
from .views import allocate_staff, timetable,allotted,delete_allotment,allot_subject_entry,timetableexcel,export_lab_allotments_csv,delete_subject_entry_view,show_google_login_page,google_auth_callback,quick_allocate,quick_delete_staff,get_free_staff,drag_action,transfer_to_staff,undo_last_action, manage_batches, subject_entry_view, get_batch_subjects, get_batch_allotments
from django.contrib.auth import views as auth_views
from .views import logout_view,dashboard_view,get_allotments_by_staff,timetableexcel_combined,download_subject_entries_csv,timetable2, download_timetable_csv,edit_staff_config,apply_ai_allocation,subject_faculty_mapping,staff_subject_count,get_subject_load,get_staff_day_load,download_staff_allotment_csv,subject_wise_allocation,export_final_workload

urlpatterns = [
    path('timetable/', timetable, name='timetable'),
    path("timetable2/", timetable2, name="timetable_auto"),

    path('allocate/', allocate_staff, name='allocate'),
    path('allotted/', allotted, name='alloctted_staff'),
<<<<<<< HEAD
    path('delete_entry/<int:entry_id>/', delete_allotment, name='delete_entry'),
    path('drag_action/<str:action>/<int:id1>/<int:id2>/', drag_action, name='drag_action'),
    path('transfer_to_staff/<int:entry_id>/<int:staff_id>/', transfer_to_staff, name='transfer_to_staff'),
    path('undo_last_action/', undo_last_action, name='undo_last_action'),
    path('subject/', allot_subject_entry, name='allot_subject_entry'),
    path('labd/',  timetableexcel, name='Lab Download'),
    path('labdfull/',  timetableexcel_combined, name='Lab Download Combined'),
    path('labd2/',  export_lab_allotments_csv, name='Lab Download 2'),
    path("download_staff_allotment/", download_staff_allotment_csv, name="download_staff_allotment"),
    path("download_subject_entries/", download_subject_entries_csv, name="download_subject_entries"),
    path("download-timetable/", download_timetable_csv, name="download_timetable"),
   
		#path('download_all_staff_jpegs/', download_all_staff_jpegs, name='download_all_staff_jpegs'),
		path("subject_wise/", subject_wise_allocation, name="subject_wise_allocation"),



    path('dsubject/', delete_subject_entry_view, name='delete_subject'),
    #path('', show_google_login_page, name='login_page'),  # home page = login
    path('google-auth-callback/', google_auth_callback, name='google_login'),
    path('', auth_views.LoginView.as_view(template_name='login.html'), name='login'),
    path('logout_old/', auth_views.LogoutView.as_view(template_name='login.html'), name='logout'),
    path('logout/', logout_view ,name='logout'),
   path('dashboard/', dashboard_view, name='dashboard'),
   path('get-allotments/', get_allotments_by_staff, name='get_allotments'),

    path('pwd/', auth_views.PasswordChangeView.as_view(
        template_name='password_change.html',
        success_url='/password-change-done/'
    ), name='password_change'),

    path('password-change-done/', auth_views.PasswordChangeDoneView.as_view(
        template_name='password_change_done.html'
    ), name='password_change_done'),

path("allotted/", allotted, name="allotted"),
path("get_free_staff/<int:subject_id>/", get_free_staff, name="get_free_staff"),


    path("quick_allocate/<int:subject_id>/<int:staff_id>/", quick_allocate, name="quick_allocate"),
path("quick_delete/<int:staff_id>/<int:subject_id>/", quick_delete_staff, name="quick_delete_staff"),
path("edit_config/", edit_staff_config, name="staff_config"),
path('apply_ai_allocation/', apply_ai_allocation, name='apply_ai_allocation'),

    path('staff_subject_count/', staff_subject_count, name='staff_subject_count'),
    path('get_subject_load/<int:staff_id>/<int:subject_id>/', get_subject_load),
    path("staff_day_load/<int:staff_id>/<str:day>/", get_staff_day_load),
    
    path("faculty-mapping/", subject_faculty_mapping, name="subject_faculty_mapping"),
    #excel workload download
    path("export-workload/", export_final_workload,name="workload_excel"),
    path('manage-batches/', manage_batches, name='manage_batches'),
    path('subject-entry/', subject_entry_view, name='subject_entry'),
    path('api/batch/<int:batch_id>/subjects/', get_batch_subjects, name='get_batch_subjects'),
    path('api/batch/<int:batch_id>/allotments/', get_batch_allotments, name='get_batch_allotments'),
]
