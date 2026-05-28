from django.http import HttpResponse
from django.shortcuts import redirect, render

from fisat.forms import AllocationForm
from .models import Staff, SubjectEntry, TimetableEntry, Batch, BatchSubject
from django.http import JsonResponse




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
                        timetable_slots[row_index][col_index] = {'totalhour':totalhour ,'class_name':subject_entry.class_name,'subject': subject_entry.subject_name, 'colspan': end_hour - start_hour + 1}
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

def manage_batches(request):
    if request.method == "POST":
        action = request.POST.get('action')
        if action == "add_batch":
            batch_name = request.POST.get('batch_name')
            if batch_name:
                Batch.objects.get_or_create(name=batch_name)
        elif action == "add_subject":
            batch_id = request.POST.get('batch_id')
            subject_name = request.POST.get('subject_name')
            if batch_id and subject_name:
                batch = Batch.objects.get(id=batch_id)
                BatchSubject.objects.create(batch=batch, subject_name=subject_name)
        return redirect('manage_batches')
    
    batches = Batch.objects.prefetch_related('subjects').all()
    return render(request, 'manage_batches.html', {'batches': batches})

def subject_entry_view(request):
    if request.method == "POST":
        batch_id = request.POST.get('batch_id')
        subject_name = request.POST.get('subject_name')
        if batch_id and subject_name:
            batch = Batch.objects.get(id=batch_id)
            
            day_1 = request.POST.get('day_1')
            hours_1 = request.POST.get('hours_1')
            if day_1 and hours_1:
                SubjectEntry.objects.create(subject_name=subject_name, class_name=batch.name, day=day_1, allotted_hours=hours_1)
            
            day_2 = request.POST.get('day_2')
            hours_2 = request.POST.get('hours_2')
            if day_2 and hours_2:
                SubjectEntry.objects.create(subject_name=subject_name, class_name=batch.name, day=day_2, allotted_hours=hours_2)
            
            return redirect('subject_entry')
    
    batches = Batch.objects.all()
    return render(request, 'subject_entry.html', {'batches': batches})

def get_batch_subjects(request, batch_id):
    subjects = BatchSubject.objects.filter(batch_id=batch_id).values('id', 'subject_name')
    return JsonResponse({'subjects': list(subjects)})

def get_batch_allotments(request, batch_id):
    try:
        batch = Batch.objects.get(id=batch_id)
        allotments = SubjectEntry.objects.filter(class_name=batch.name).values('id', 'subject_name', 'day', 'allotted_hours')
        # map day codes to display names
        day_map = dict(SubjectEntry.DAY_CHOICES)
        allotments_list = list(allotments)
        for a in allotments_list:
            a['day_display'] = day_map.get(a['day'], a['day'])
        return JsonResponse({'allotments': allotments_list})
    except Batch.DoesNotExist:
        return JsonResponse({'allotments': []})
