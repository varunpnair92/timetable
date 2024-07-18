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

    
    def __str__(self):
        return f"{self.subject_name} - {self.class_name}"
    
    class Meta:
        db_table = 'subjectentry'
        unique_together = (('day', 'LAB','allotted_hours'),)
        
        
    def clean(self):
        if self.pk is None:  # Check if this is a new instance (not updating)
            existing_entries = SubjectEntry.objects.filter(day=self.day, LAB=self.LAB)
            for entry in existing_entries:
                existing_hours = set(map(int, entry.allotted_hours.split(',')))
                new_hours = set(map(int, self.allotted_hours.split(',')))
                if existing_hours.intersection(new_hours):
                    raise ValidationError(_('Overlapping hours are not allowed within the same day and lab.'))
    
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
                    raise ValidationError(
                        f'Staff {self.staff.name} is already assigned to hours {existing_hours.intersection(subject_hours)} '
                        f'on {entry.subject.get_day_display()}. Cannot allocate overlapping hours.'
                    )

    def save(self, *args, **kwargs):
        self.full_clean()  # Ensure model passes validation before saving
        super().save(*args, **kwargs)
