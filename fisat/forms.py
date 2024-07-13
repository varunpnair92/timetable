from django import forms
from .models import Staff, SubjectEntry, TimetableEntry

class SubjectEntryChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.subject_name} - {obj.class_name} - {obj.allotted_hours} ({obj.get_day_display()})"

class AllocationForm(forms.ModelForm):
    staff = forms.ModelChoiceField(queryset=Staff.objects.all(), label="Select Staff")
    subject_entry = SubjectEntryChoiceField(queryset=SubjectEntry.objects.all(), label="Select Subject")

    class Meta:
        model = TimetableEntry
        fields = ['staff', 'subject_entry']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['subject_entry'].queryset = SubjectEntry.objects.all()
