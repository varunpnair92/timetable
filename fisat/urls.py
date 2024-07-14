from django.urls import path
from .views import allocate_staff, timetable,allotted,delete_allotment

urlpatterns = [
    path('', timetable, name='timetable'),
    path('allocate/', allocate_staff, name='allocate_staff'),
    path('allotted/', allotted, name='alloctted_staff'),
    path('delete_entry/<int:entry_id>/', delete_allotment, name='delete_entry'),
]
