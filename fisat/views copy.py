from django.http import HttpResponse
from django.shortcuts import redirect, render

from fisat.forms import AllocationForm
from .models import Staff, SubjectEntry, TimetableEntry




from django.shortcuts import render, HttpResponse
from .forms import AllocationForm

def allocate_staff(request):
    if request.method == "POST":
        form = AllocationForm(request.POST)
        if form.is_valid():
            # Get the subject_entry instance from the form
            subject_entry = form.cleaned_data['subject_entry']
            
            # Create a new TimetableEntry instance with staff and subject_entry
            TimetableEntry.objects.create(
                staff=form.cleaned_data['staff'],
                subject=subject_entry,
            )
            return HttpResponse('Allocation successful!')  # Return a success message
    else:
        form = AllocationForm()  # Initialize the form

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
                        timetable_slots[row_index][col_index] = {'subject': subject_entry.subject_name, 'colspan': end_hour - start_hour + 1}
                    else:
                        timetable_slots[row_index][col_index] = None

        # Store staff timetable in the dictionary
        staff_timetables[staff.name] = timetable_slots

    # Render the template with staff timetables data
    return render(request, 'timetable.html', {'staff_timetables': staff_timetables})

