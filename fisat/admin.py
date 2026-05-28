from django.contrib import admin
from .models import SubjectEntry,Staff,TimetableEntry, Batch, BatchSubject

admin.site.register(SubjectEntry)
admin.site.register(Staff)
admin.site.register(TimetableEntry)
admin.site.register(Batch)
admin.site.register(BatchSubject)



