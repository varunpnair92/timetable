from pyexpat.errors import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from fisat.forms import AllocationForm
from .models import Staff, SubjectEntry, TimetableEntry




from django.shortcuts import render, HttpResponse
from .forms import AllocationForm

from django.shortcuts import render, redirect
from django.urls import reverse
from .forms import AllocationForm
from .models import TimetableEntry

def allocate_staff(request):
    if request.method == "POST":
        action = request.POST.get('action')
        form = AllocationForm(request.POST, action=action)
        
        if form.is_valid():
            if action == 'delete':
                delete_entry = form.cleaned_data['delete_entry']
                if delete_entry:
                    delete_entry.delete()
            elif action == 'allot':
                form.save()
            return redirect(reverse('timetable'))  # Redirect to your timetable view or another appropriate view
    else:
        form = AllocationForm()

    return render(request, 'allocate.html', {'form': form})








# views.py

from django.shortcuts import render
from .models import Staff, TimetableEntry

def timetable(request):
    # Fetch all staff members
    staff_members = Staff.objects.all()

    # Prepare a dictionary to store staff allocations
    staff_timetables = {}

    # Iterate over each staff member
    for staff in staff_members:
        # Initialize a 2D list (5x7) for timetable slots
        timetable_slots = [['' for _ in range(7)] for _ in range(5)]

        # Fetch TimetableEntry instances for the current staff
        timetable_entries = TimetableEntry.objects.filter(staff=staff)
        totalhour=7

        # Iterate over each TimetableEntry for the current staff
        for entry in timetable_entries:
            subject_entry = entry.subject
            allotted_hours = subject_entry.allotted_hours.split(',')  # Split hours into list
            day = subject_entry.day

            # Determine the row index based on the day
            day_to_row = {
                'M': 0,
                'T': 1,
                'W': 2,
                'Th': 3,
                'F': 4
            }
            row_index = day_to_row.get(day, None)

            if row_index is not None:
                start_hour = int(allotted_hours[0]) - 1
                end_hour = int(allotted_hours[-1]) - 1
                for col_index in range(start_hour, end_hour + 1):
                    if col_index == start_hour:
                        timetable_slots[row_index][col_index] = {'lab':subject_entry.LAB,'totalhour':totalhour ,'class_name':subject_entry.class_name,'subject': subject_entry.subject_name, 'colspan': end_hour - start_hour + 1}
                    else:
                        timetable_slots[row_index][col_index] = None

        # Store staff timetable in the dictionary
        staff_timetables[staff.name] = timetable_slots

    # Render the template with staff timetables data
    return render(request, 'timetable.html', {'staff_timetables': staff_timetables})


# views.py

from django.shortcuts import render
from .models import SubjectEntry, TimetableEntry

def allotted(request):
    subjects = SubjectEntry.objects.all()

    # Organize data by class
    class_data = {}

    for subject in subjects:
        class_name = subject.class_name

        if class_name not in class_data:
            class_data[class_name] = []

        timetable_entries = TimetableEntry.objects.filter(subject=subject)

        staff_names = []
        for entry in timetable_entries:
            staff_names.append(entry.staff.name)

        class_data[class_name].append({
            'subject_name': subject.subject_name,
            'staff_names': ', '.join(staff_names),  # Combine staff names into a string
            'allotted_hours': subject.allotted_hours
        })

    return render(request, 'allotted.html', {'class_data': class_data})

def delete_allotment(request, entry_id):
    entry = get_object_or_404(TimetableEntry, id=entry_id)
    entry.delete()
    return redirect('timetable')  
    

#subjectentry
from django.http import HttpResponse
from django.shortcuts import render, redirect
from .forms import SubjectEntryForm

def allot_subject_entry(request):
    if request.method == 'POST':
        form = SubjectEntryForm(request.POST)
        if form.is_valid():
            form.save()
            return HttpResponse("Success! Subject entries have been allotted.")
    else:
        form = SubjectEntryForm()
    return render(request, 'subject.html', {'form': form})

