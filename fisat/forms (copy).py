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
        self.fields['subject_entry'].queryset = SubjectEntry.objects.all()
        self.fields['delete_entry'].queryset = TimetableEntry.objects.all()

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
