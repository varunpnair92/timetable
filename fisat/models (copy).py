from django.db import models
from django.forms import ValidationError

class SubjectEntry(models.Model):
    _id = models.AutoField(primary_key=True,db_column="tid")
    subject_name = models.CharField(max_length=100)
    class_name = models.CharField(max_length=100)
    DAY_CHOICES = (
        ('M', 'Monday'),
        ('T', 'Tuesday'),
        ('W', 'Wednesday'),
        ('Th', 'Thursday'),
        ('F', 'Friday'),
    )
    
    day = models.CharField(max_length=2, choices=DAY_CHOICES)
    allotted_hours = models.CharField(max_length=10)  # e.g., '1,2,3' or '4,5,6'

    def __str__(self):
        return f"{self.subject_name} - {self.class_name}"
    class Meta:
        db_table = 'subjectentry'
    
class Staff(models.Model):
    _id = models.AutoField(primary_key=True,db_column="sid")
    name = models.CharField(max_length=100)
    
    def __str__(self):
        return self.name
    class Meta:
        db_table = 'staff'


class TimetableEntry(models.Model):
    _id = models.AutoField(primary_key=True, db_column="tid")
    staff = models.ForeignKey(Staff, on_delete=models.CASCADE, db_column="staffid")
    subject = models.ForeignKey(SubjectEntry, on_delete=models.CASCADE, db_column="subjectid")
    

    def __str__(self):
        return f"{self.staff.name} - {self.subject.subject_name} "

    class Meta:
        db_table = 'timetableentry'
        unique_together = (('staff', 'subject'),)
    def clean(self):
        # Check if subject is assigned to more than 2 staff members
        if self.subject_id is not None:
            existing_entries = TimetableEntry.objects.filter(subject=self.subject)
            if existing_entries.count() >= 2:
                raise ValidationError('This subject already has two staff members assigned.')

        # Check for overlapping hours for the same staff on the same day
        if self.staff_id is not None and self.subject_id is not None:
            subject_day = self.subject.day
            subject_hours = set(map(int, self.subject.allotted_hours.split(',')))
            existing_entries = TimetableEntry.objects.filter(staff=self.staff, subject__day=subject_day)

            for entry in existing_entries:
                existing_hours = set(map(int, entry.subject.allotted_hours.split(',')))
                if subject_hours.intersection(existing_hours):
                    raise ValidationError(f'Staff {self.staff.name} is already assigned to hours {existing_hours.intersection(subject_hours)} on {entry.subject.get_day_display()}.')

    def save(self, *args, **kwargs):
        self.full_clean()  # Ensure model passes validation before saving
        super().save(*args, **kwargs)
