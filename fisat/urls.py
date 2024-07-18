from django.urls import path,include
from .views import allocate_staff, timetable,allotted,delete_allotment,allot_subject_entry

urlpatterns = [
    path('', timetable, name='timetable'),
    path('allocate/', allocate_staff, name='allocate_staff'),
    path('allotted/', allotted, name='alloctted_staff'),
    path('delete_entry/<int:entry_id>/', delete_allotment, name='delete_entry'),
    path('subject/', allot_subject_entry, name='allot_subject_entry'),

]
