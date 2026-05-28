from django.urls import path
from .views import allocate_staff, timetable, allotted, manage_batches, subject_entry_view, get_batch_subjects, get_batch_allotments

urlpatterns = [
    path('', timetable, name='timetable'),
    path('allocate/', allocate_staff, name='allocate_staff'),
    path('allotted/', allotted, name='alloctted_staff'),
    path('manage-batches/', manage_batches, name='manage_batches'),
    path('subject-entry/', subject_entry_view, name='subject_entry'),
    path('api/batch/<int:batch_id>/subjects/', get_batch_subjects, name='get_batch_subjects'),
    path('api/batch/<int:batch_id>/allotments/', get_batch_allotments, name='get_batch_allotments'),
]
