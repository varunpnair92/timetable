from django import forms
from .models import Staff, SubjectEntry, TimetableEntry

class SubjectEntryChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.subject_name} - {obj.class_name} - {obj.allotted_hours} ({obj.get_day_display()})"

class TimetableEntryChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.staff.name} - {obj.subject.subject_name} - {obj.subject.class_name} ({obj.subject.get_day_display()})"

class AllocationForm(forms.ModelForm):
    staff = forms.ModelChoiceField(queryset=Staff.objects.all(), label="Select Staff")
    subject_entry = SubjectEntryChoiceField(queryset=SubjectEntry.objects.all(), label="Select Subject", required=False)
    delete_entry = TimetableEntryChoiceField(queryset=TimetableEntry.objects.all(), required=False, label="Select Allotment to Delete")

    class Meta:
        model = TimetableEntry
        fields = ['staff', 'subject_entry']

    def __init__(self, *args, **kwargs):
        action = kwargs.pop('action', None)
        super().__init__(*args, **kwargs)
        #self.fields['subject_entry'].queryset = SubjectEntry.objects.all()
        # Sort SubjectEntry queryset by subject name and class name
        self.fields['subject_entry'].queryset = SubjectEntry.objects.all().order_by('subject_name', 'class_name')
        #self.fields['delete_entry'].queryset = TimetableEntry.objects.all()
        # Sort TimetableEntry queryset by staff name
        self.fields['delete_entry'].queryset = TimetableEntry.objects.all().order_by('staff__name')

        if action == 'allot':
            self.fields['subject_entry'].required = True
            self.fields['delete_entry'].required = False
        elif action == 'delete':
            self.fields['subject_entry'].required = False
            self.fields['delete_entry'].required = True

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.subject = self.cleaned_data['subject_entry']
        if commit:
            instance.save()
        return instance



#subject entry
# forms.py
from django import forms
from .models import SubjectEntry

class SubjectEntryForm(forms.ModelForm):
    day_1 = forms.ChoiceField(choices=SubjectEntry.DAY_CHOICES, label="Day 1")
    hours_1 = forms.CharField(max_length=50, required=False, label="Allotted Hours 1")
    day_2 = forms.ChoiceField(choices=SubjectEntry.DAY_CHOICES, required=False, label="Day 2")
    hours_2 = forms.CharField(max_length=50, required=False, label="Allotted Hours 2")

    class Meta:
        model = SubjectEntry
        fields = ['subject_name', 'class_name', 'LAB']

    def save(self, commit=True):
        instance = super().save(commit=False)
        
        # Check if both day and hour fields are filled for second allotment
        if self.cleaned_data['day_2'] and self.cleaned_data['hours_2']:
            days_hours = [
                (self.cleaned_data['day_1'], self.cleaned_data['hours_1']),
                (self.cleaned_data['day_2'], self.cleaned_data['hours_2'])
            ]
        else:
            days_hours = [(self.cleaned_data['day_1'], self.cleaned_data['hours_1'])]

        created_instances = []

        for day, hour in days_hours:
            if day and hour:  # Check if both day and hour fields are not empty
                new_instance = SubjectEntry(
                    subject_name=instance.subject_name,
                    class_name=instance.class_name,
                    day=day,
                    LAB=instance.LAB,
                    allotted_hours=hour
                )
                if commit:
                    new_instance.save()
                    created_instances.append(new_instance)

        return created_instances


