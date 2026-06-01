import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'fisatlab.settings')
django.setup()

from fisat.models import Batch, BatchSubject, SubjectEntry

print("All Subject Entries for S2 MCA:")
entries = SubjectEntry.objects.filter(class_name__icontains='s2 mca')
for e in entries:
    print(f"Sub: {e.subject_name}, Lab: {e.LAB}, Day: {e.day}, Hours: {e.allotted_hours}")
    
batches = Batch.objects.filter(name__icontains='mca')
for b in batches:
    print(f"\nBatch: {b.name}")
    for s in b.subjects.all():
        print(f"  - {s.subject_name}")
