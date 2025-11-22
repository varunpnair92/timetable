from django.urls import path,include
from .views import allocate_staff, timetable,allotted,delete_allotment,allot_subject_entry,timetableexcel,export_lab_allotments_csv,delete_subject_entry_view,show_google_login_page,google_auth_callback,quick_allocate,quick_delete_staff,get_free_staff
from django.contrib.auth import views as auth_views
from .views import logout_view,dashboard_view,get_allotments_by_staff,timetableexcel_combined,timetable2,edit_staff_config

urlpatterns = [
    path('timetable/', timetable, name='timetable'),
    path("timetable2/", timetable2, name="timetable_auto"),

    path('allocate/', allocate_staff, name='allocate'),
    path('allotted/', allotted, name='alloctted_staff'),
    path('delete_entry/<int:entry_id>/', delete_allotment, name='delete_entry'),
    path('subject/', allot_subject_entry, name='allot_subject_entry'),
    path('labd/',  timetableexcel, name='Lab Download'),
    path('labdfull/',  timetableexcel_combined, name='Lab Download Combined'),
    path('labd2/',  export_lab_allotments_csv, name='Lab Download 2'),
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



]
