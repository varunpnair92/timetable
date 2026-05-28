from django.db import models
from django.forms import ValidationError
from django.conf import settings
from django.contrib.auth.models import User

class Semester(models.Model):
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=False)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if self.is_active:
            Semester.objects.filter(is_active=True).exclude(pk=self.pk).update(is_active=False)
        super().save(*args, **kwargs)

    class Meta:
        db_table = 'semester'

class Batch(models.Model):
    name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name
    class Meta:
        db_table = 'batch'

class BatchSubject(models.Model):
    batch = models.ForeignKey(Batch, related_name='subjects', on_delete=models.CASCADE)
    subject_name = models.CharField(max_length=100)

    def __str__(self):
        return f"{self.subject_name} ({self.batch.name})"
    class Meta:
        db_table = 'batchsubject'
class SubjectEntry(models.Model):
    id = models.AutoField(primary_key=True,db_column="tid")
    subject_name = models.CharField(max_length=100)
    class_name = models.CharField(max_length=100)
    DAY_CHOICES = (
        ('M', 'Monday'),
        ('T', 'Tuesday'),
        ('W', 'Wednesday'),
        ('Th', 'Thursday'),
        ('F', 'Friday'),
    )
    LAB_CHOICES = (
        ('L1', 'CCF L1'),
        ('L2', 'CCF L2'),
        ('L3', 'CCF L3'),
        ('L4', 'CCF L4'),
        ('L5', 'CCF L5'),
        ('L6', 'CCF L6'),
        ('L7', 'CCF L7'),
        ('L8', 'CCF L8'),
        ('L9', 'CCF L9'),
        ('MP LAB', 'MICRO PROCESSOR LAB'),
        ('PG LAB', 'PG LAB'),
    )
    
    day = models.CharField(max_length=10, choices=DAY_CHOICES)
    LAB = models.CharField(max_length=50, choices=LAB_CHOICES)
    allotted_hours = models.CharField(max_length=10)  # e.g., '1,2,3' or '4,5,6'
    period = models.CharField(max_length=20, default='2025-dec')


    
    def __str__(self):
        return f"{self.subject_name} - {self.class_name}"
    
    class Meta:
        db_table = 'subjectentry'
        unique_together = (('day', 'LAB','allotted_hours','period'),)
        
        
    def clean(self):
        if self.pk is None:  # Check if this is a new instance (not updating)
            existing_entries = SubjectEntry.objects.filter(day=self.day, LAB=self.LAB, period=self.period)
            for entry in existing_entries:
                existing_hours = set(map(int, entry.allotted_hours.split(',')))
                new_hours = set(map(int, self.allotted_hours.split(',')))
                if existing_hours.intersection(new_hours):
                    raise ValidationError('Overlapping hours are not allowed within the same day and lab.')
    
class Staff(models.Model):
    id = models.AutoField(primary_key=True,db_column="sid")
    name = models.CharField(max_length=100)
    
    def __str__(self):
        return self.name
    class Meta:
        db_table = 'staff'


class TimetableEntry(models.Model):
    id = models.AutoField(primary_key=True, db_column="tid")
    staff = models.ForeignKey(Staff, on_delete=models.CASCADE, db_column="staffid")
    subject = models.ForeignKey(SubjectEntry, on_delete=models.CASCADE, db_column="subjectid")
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)

    def __str__(self):
        return f"{self.staff.name} - {self.subject.subject_name} "

    class Meta:
        db_table = 'timetableentry'
        unique_together = (('staff', 'subject'),)
    def clean(self):
        # Check if subject is assigned to more than 2 staff members
        if self.subject_id is not None:
            existing_entries = TimetableEntry.objects.filter(subject=self.subject, subject__period=self.subject.period)
            if existing_entries.count() >= 2:
                raise ValidationError('This subject already has two staff members assigned.')

        # Check for overlapping hours for the same staff on the same day
        if self.staff_id is not None and self.subject_id is not None:
            subject_day = self.subject.day
            subject_hours = set(map(int, self.subject.allotted_hours.split(',')))
            existing_entries = TimetableEntry.objects.filter(staff=self.staff, subject__day=subject_day, subject__period=self.subject.period)

            for entry in existing_entries:
                existing_hours = set(map(int, entry.subject.allotted_hours.split(',')))
                if subject_hours.intersection(existing_hours):
                    raise ValidationError(
                        f'Staff {self.staff.name} is already assigned to hours {existing_hours.intersection(subject_hours)} '
                        f'on {entry.subject.get_day_display()}. Cannot allocate overlapping hours.'
                    )

    def save(self, *args, **kwargs):
        self.full_clean()  # Ensure model passes validation before saving
        super().save(*args, **kwargs)
        
class SubjectFacultyMap(models.Model):
    subject = models.ForeignKey(SubjectEntry, on_delete=models.CASCADE)
    faculty_names = models.TextField()
    period = models.CharField(max_length=10)

    def __str__(self):
        return f"{self.subject.subject_name} → {self.faculty_names}"

        
