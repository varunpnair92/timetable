from django.urls import path,include
from .views import allocate_staff, timetable,allotted,delete_allotment,allot_subject_entry,timetableexcel,export_lab_allotments_csv,delete_subject_entry_view,show_google_login_page,google_auth_callback
from django.contrib.auth import views as auth_views
from .views import logout_view,dashboard_view

urlpatterns = [
    path('timetable/', timetable, name='timetable'),
    path('allocate/', allocate_staff, name='allocate'),
    path('allotted/', allotted, name='alloctted_staff'),
    path('delete_entry/<int:entry_id>/', delete_allotment, name='delete_entry'),
    path('subject/', allot_subject_entry, name='allot_subject_entry'),
    path('labd/',  timetableexcel, name='Lab Download'),
    path('labd2/',  export_lab_allotments_csv, name='Lab Download 2'),
    path('dsubject/', delete_subject_entry_view, name='delete_subject'),
    #path('', show_google_login_page, name='login_page'),  # home page = login
    path('google-auth-callback/', google_auth_callback, name='google_login'),
    path('', auth_views.LoginView.as_view(template_name='login.html'), name='login'),
    path('logout_old/', auth_views.LogoutView.as_view(template_name='login.html'), name='logout'),
    path('logout/', logout_view ,name='logout'),
   path('dashboard/', dashboard_view, name='dashboard'),
    path('pwd/', auth_views.PasswordChangeView.as_view(
        template_name='password_change.html',
        success_url='/password-change-done/'
    ), name='password_change'),

    path('password-change-done/', auth_views.PasswordChangeDoneView.as_view(
        template_name='password_change_done.html'
    ), name='password_change_done'),

]
