from django.urls import path,include
from .views import allocate_staff, timetable,allotted,delete_allotment,allot_subject_entry,timetableexcel,export_lab_allotments_csv

urlpatterns = [
    path('', timetable, name='timetable'),
    path('allocate/', allocate_staff, name='allocate_staff'),
    path('allotted/', allotted, name='alloctted_staff'),
    path('delete_entry/<int:entry_id>/', delete_allotment, name='delete_entry'),
    path('subject/', allot_subject_entry, name='allot_subject_entry'),
    path('labd/',  timetableexcel, name='Lab Download'),
    path('labd2/',  export_lab_allotments_csv, name='Lab Download 2'),
   

]
