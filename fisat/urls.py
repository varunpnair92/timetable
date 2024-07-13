from django.urls import path
from .views import allocate_staff, timetable

urlpatterns = [
    path('', timetable, name='timetable'),
    path('allocate/', allocate_staff, name='allocate_staff'),
]
