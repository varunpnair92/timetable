from pyexpat.errors import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from fisat.forms import AllocationForm
from .models import Staff, SubjectEntry, TimetableEntry
from django.conf import settings

from django.contrib.auth.decorators import login_required

from django.shortcuts import render, HttpResponse
from .forms import AllocationForm

from django.shortcuts import render, redirect
from django.urls import reverse
from .forms import AllocationForm
from .models import TimetableEntry
from django.contrib.auth import logout

DP=settings.DP


@login_required(login_url='/')
def allocate_staff(request):
    action = request.POST.get('action') if request.method == "POST" else request.GET.get('action', 'allot')

    if request.method == "POST":
        form = AllocationForm(request.POST, action=action, user=request.user)
        if form.is_valid():
            if action == 'delete':
                delete_entry = form.cleaned_data['delete_entry']
                if delete_entry:
                    delete_entry.delete()
            elif action == 'allot':
                form.save()
            return redirect(reverse('timetable'))  # or any relevant success URL
    else:
        form = AllocationForm(action=action, user=request.user)  # updated here ✅

    return render(request, 'allocate.html', {'form': form, 'action': action})









# views.py

from django.shortcuts import render
from .models import Staff, TimetableEntry
@login_required(login_url='/')
def timetable(request):
    # Fetch all staff members
    staff_members = Staff.objects.all()

    # Prepare a dictionary to store staff allocations
    staff_timetables = {}

    # Iterate over each staff member
    for staff in staff_members:
        # Initialize a 2D list (5x8) for timetable slots (8 columns to include the shifted hours)
        timetable_slots = [['' for _ in range(8)] for _ in range(5)]

        # Fetch TimetableEntry instances for the current staff
        timetable_entries = TimetableEntry.objects.filter(staff=staff,subject__period=DP,user_id=request.user)
        workload = 0

        # Iterate over each TimetableEntry for the current staff
        for entry in timetable_entries:
            subject_entry = entry.subject
            allotted_hours = subject_entry.allotted_hours.split(',')  # Split hours into list
            day = subject_entry.day
            workload += len(allotted_hours)

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
                # Adjust hours if '8' (LB) is present and increment hours greater than 4
                adjusted_hours = []
                for hour in allotted_hours:
                    if hour == '8':
                        adjusted_hours.append('5')
                    elif hour== '5':
                        #adjusted_hours.append(str(int(hour) + 1))
                        adjusted_hours.append('6')
                    elif hour== '6':
                        #adjusted_hours.append(str(int(hour) + 1))
                        adjusted_hours.append('7')
                    elif hour== '7':
                        #adjusted_hours.append(str(int(hour) + 1))
                        adjusted_hours.append('8')
                    else:
                        adjusted_hours.append(hour)

                # Remove duplicates and sort the hours to handle merging
                adjusted_hours = sorted(set(adjusted_hours), key=lambda x: int(x))

                start_index = int(adjusted_hours[0]) - 1
                end_index = int(adjusted_hours[-1]) - 1

                # Ensure start_index and end_index are within valid range
                start_index = min(start_index, 7)
                end_index = min(end_index, 7)

                # Ensure end_index is not less than start_index
                if end_index < start_index:
                    end_index = start_index

                # Adjust for shifting hours after the 4th hour
                

                # Ensure the indices are within the valid range
                if start_index >= 0 and end_index < 8:
                    # Merge cells correctly
                    for col_index in range(start_index, end_index + 1):
                        if col_index == start_index:
                            timetable_slots[row_index][col_index] = {
                                'lab': subject_entry.LAB,
                                'class_name': subject_entry.class_name,
                                'subject': subject_entry.subject_name,
                                'colspan': end_index - start_index + 1
                            }
                        else:
                            if col_index < 8:  # Ensure col_index does not exceed the list size
                                timetable_slots[row_index][col_index] = None
                else:
                    print(f"Index out of range: start_index={start_index}, end_index={end_index}")

        # Store staff timetable in the dictionary
        staff_timetables[staff.name] = {
            'timetable_slots': timetable_slots,
            'total_hour': workload
        }

    # Render the template with staff timetables data
    return render(request, 'timetable.html', {'staff_timetables': staff_timetables})



# views.py

from django.shortcuts import render
from .models import SubjectEntry, TimetableEntry
# @login_required(login_url='/')
# def allotted(request):
#     subjects = SubjectEntry.objects.filter(period=DP)

#     # Organize data by class
#     class_data = {}

#     for subject in subjects:
#         class_name = subject.class_name

#         if class_name not in class_data:
#             class_data[class_name] = []

#         timetable_entries = TimetableEntry.objects.filter(subject=subject,subject__period=DP,user_id=request.user)

#         staff_names = []
#         for entry in timetable_entries:
#             staff_names.append(entry.staff.name)

#         class_data[class_name].append({
#             'subject_name': subject.subject_name,
#             'staff_names': ', '.join(staff_names),  # Combine staff names into a string
#             'allotted_hours': subject.allotted_hours,
#             'day': subject.day
            
#         })

#     return render(request, 'allotted.html', {'class_data': class_data})
@login_required(login_url='/')
def allotted(request):
    subjects = SubjectEntry.objects.filter(period=DP)
    staff_list = Staff.objects.all()

    class_data = {}

    for subject in subjects:
        class_name = subject.class_name

        if class_name not in class_data:
            class_data[class_name] = []

        # Already allotted staff
        allocations = TimetableEntry.objects.filter(
            subject=subject,
            subject__period=DP
        )

        allocated_staff = [a.staff for a in allocations]

        class_data[class_name].append({
            'subject_id': subject.id,
            'subject_name': subject.subject_name,
            'allocated_staff': allocated_staff,
            'all_staff': staff_list,  # used when clicking +
            'day': subject.day,
            'allotted_hours': subject.allotted_hours,
        })

    return render(request, 'allotted.html', {
        'class_data': class_data
    })



from django.http import JsonResponse

@login_required
def get_free_staff(request, subject_id):
    subject = SubjectEntry.objects.get(id=subject_id)

    # Day & hours of selected subject
    target_day = subject.day
    target_hours = set(map(int, subject.allotted_hours.split(',')))

    # All staff
    staff_list = Staff.objects.all()
    free_staff = []

    for st in staff_list:
        # Get all entries for this staff on same day/period
        entries = TimetableEntry.objects.filter(
            staff=st,
            subject__day=target_day,
            subject__period=DP
        )

        # Check if staff is free
        busy = False
        for e in entries:
            entry_hours = set(map(int, e.subject.allotted_hours.split(',')))
            if target_hours.intersection(entry_hours):
                busy = True
                break

        if not busy:
            free_staff.append({"id": st.id, "name": st.name})

    return JsonResponse(free_staff, safe=False)





@login_required(login_url='/')
def delete_allotment(request, entry_id):
    entry = get_object_or_404(TimetableEntry, id=entry_id)
    entry.delete()
    return redirect('timetable')  
    

#subjectentry
from django.http import HttpResponse
from django.shortcuts import render, redirect
from .forms import SubjectEntryForm
@login_required(login_url='')
def allot_subject_entry(request):
    if request.method == 'POST':
        form = SubjectEntryForm(request.POST)
        if form.is_valid():
            form.save()
            return HttpResponse("Success! Subject entries have been allotted.")
    else:
        form = SubjectEntryForm()
    return render(request, 'subject.html', {'form': form})
    
    
#timetableexcel   
import io
import xlsxwriter
from django.http import HttpResponse
from .models import SubjectEntry, TimetableEntry
@login_required(login_url='/')
def timetableexcel(request):
    # Get distinct lab names
    labs = SubjectEntry.objects.filter(period=DP).values_list('LAB', flat=True).distinct()


    # Create an in-memory output stream for storing the Excel file
    output = io.BytesIO()

    # Create a workbook
    workbook = xlsxwriter.Workbook(output)

    # Define formats for headers, data, merged cells, and empty slots
    header_format = workbook.add_format({'bold': True, 'align': 'center', 'valign': 'vcenter', 'bg_color': '#F2F2F2', 'border': 1})
    data_format = workbook.add_format({'align': 'center', 'valign': 'vcenter', 'border': 1})
    merge_format = workbook.add_format({'align': 'center', 'valign': 'vcenter', 'border': 1})
    empty_slot_format = workbook.add_format({'bg_color': '#D3D3D3', 'border': 1})

    # Define staff name abbreviations
    staff_abbreviations = {
        "AMBILI N MENON": "ANM",
        "SREELALITHAMBIKA P K": "SL",
        "SANDHYA O C": "SOC",
        "NEEBA CHERIYACHAN": "NC",
        "NOMA MATHEW M": "NM",
        "AMBILY SEKAR C": "AS",
        "VARUN P NAIR": "VPN",
        "ARAVIND BALAN": "AB",
        "SALINIT T R": "STR",
        "SMIJA": "SM",
        "JOICY": "JY"
    }

    # Define days of the week and hours
    days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
    hours = ['H1', 'H2', 'H3', 'H4', 'LB', 'H5', 'H6', 'H7']

    day_mapping = {
        'M': 'Mon',
        'T': 'Tue',
        'W': 'Wed',
        'Th': 'Thu',
        'F': 'Fri'
    }

    # Create a worksheet for each lab
    for lab in labs:
        # Add a worksheet for the current lab
        worksheet = workbook.add_worksheet(lab)

        # Write headers
        worksheet.write(0, 0, 'Day', header_format)
        for col, hour in enumerate(hours):
            worksheet.write(0, col + 1, hour, header_format)

        # Initialize row index
        row_index = 1

        # Query SubjectEntry objects for the current lab
        subjects = SubjectEntry.objects.filter(LAB=lab,period=DP).order_by('day')

        # Track merged cells to avoid overlaps
        merged_cells = {}

        # Populate timetable slots with data
        for day in days:
            day_key = [k for k, v in day_mapping.items() if v == day]
            if not day_key:
                continue  # Skip if the day is not found in the mapping
            day_key = day_key[0]
            day_subjects = subjects.filter(day=day_key)
            if day_subjects:
                # Write the day name in the first column
                worksheet.write(row_index, 0, day, data_format)

                for subject in day_subjects:
                    # Get TimetableEntry objects for the current subject
                    timetable_entries = TimetableEntry.objects.filter(subject=subject,subject__period=DP,user_id=request.user)

                    # Combine staff names into a single string with abbreviations
                    staff_names = ",".join(staff_abbreviations.get(entry.staff.name, entry.staff.name) for entry in timetable_entries)
                    details = f"{subject.subject_name} ({subject.class_name})\n{staff_names}"

                    # Split allotted hours into a list
                    allotted_hours = subject.allotted_hours.split(',')

                    # Adjust hours if '8' (LB) is present and increment hours greater than 4
                    adjusted_hours = []
                    for hour in allotted_hours:
                        if hour == '8':
                            adjusted_hours.append('5')
                        elif hour == '5':
                            adjusted_hours.append('6')
                        elif hour == '6':
                            adjusted_hours.append('7')
                        elif hour == '7':
                            adjusted_hours.append('8')
                        else:
                            adjusted_hours.append(hour)

                    # Remove duplicates and sort the hours to handle merging
                    adjusted_hours = sorted(set(adjusted_hours), key=lambda x: int(x))

                    start_index = int(adjusted_hours[0]) - 1
                    end_index = int(adjusted_hours[-1]) - 1

                    # Ensure the indices are within the valid range
                    if start_index >= 0 and end_index < 8:
                        # Check if the cells to be merged overlap with any existing merges
                        merge_key = (row_index, start_index + 1, row_index, end_index + 1)
                        if not any(start <= merge_key[1] <= end or start <= merge_key[3] <= end for start, end in merged_cells.get(row_index, [])):
                            # Merge cells correctly and set values
                            worksheet.merge_range(row_index, start_index + 1, row_index, end_index + 1, details, merge_format)
                            merged_cells.setdefault(row_index, []).append((start_index + 1, end_index + 1))
                        else:
                            # Write the subject details in the respective columns
                            for hour in adjusted_hours:
                                worksheet.write(row_index, int(hour), details, merge_format)

                row_index += 1

        # Set column widths and row heights
        worksheet.set_column('A:A', 5)  # Day column width
        worksheet.set_column('B:I', 8)  # Hours column width
        worksheet.set_default_row(45)  # Default row height

        # Fill empty slots with grey color
        for row in range(1, row_index):
            for col in range(1, len(hours) + 1):
                if worksheet.table.get(row, {}).get(col) is None:
                    worksheet.write(row, col, '', empty_slot_format)

    # Close workbook
    workbook.close()

    # Prepare response
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename="lab_details.xlsx"'
    output.seek(0)
    response.write(output.getvalue())

    return response


#all in one excel
@login_required(login_url='/')
def timetableexcel_combined(request):
    import io
    import xlsxwriter

    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output)
    ws = workbook.add_worksheet("Combined Labs")

    # Formats
    header_format = workbook.add_format({'bold': True, 'align': 'center',
                                         'valign': 'vcenter', 'bg_color': '#F2F2F2', 'border': 1})
    data_format = workbook.add_format({'align': 'center', 'valign': 'vcenter', 'border': 1})
    merge_format = workbook.add_format({'align': 'center', 'valign': 'vcenter', 'border': 1})
    empty_slot_format = workbook.add_format({'bg_color': '#D3D3D3', 'border': 1})

    # Staff abbreviation
    staff_abbr = {
        "AMBILI N MENON": "ANM", "SREELALITHAMBIKA P K": "SL",
        "SANDHYA O C": "SOC", "NEEBA CHERIYACHAN": "NC",
        "NOMA MATHEW M": "NM", "AMBILY SEKAR C": "AS",
        "VARUN P NAIR": "VPN", "ARAVIND BALAN": "AB",
        "SALINIT T R": "STR", "SMIJA": "SM", "JOICY": "JY"
    }

    days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
    hours = ['H1', 'H2', 'H3', 'H4', 'LB', 'H5', 'H6', 'H7']
    day_map = {'M': 'Mon', 'T': 'Tue', 'W': 'Wed', 'Th': 'Thu', 'F': 'Fri'}

    # LAB grouping order
    lab_groups = [
        ['L1', 'L2', 'L3'],
        ['L5', 'L7', 'L8'],
        ['L4', 'L6'],
        ['L9', 'PG LAB']
    ]

    start_row = 0

    for group in lab_groups:
        col_offset = 0

        for lab in group:

            col = col_offset
            subjects = SubjectEntry.objects.filter(LAB=lab, period=DP).order_by("day")

            # Write headers
            ws.write(start_row, col, lab, header_format)
            ws.write(start_row+1, col, "Day", header_format)
            for i, hr in enumerate(hours):
                ws.write(start_row+1, col+1+i, hr, header_format)

            # Track merged cells per row
            merged_cells = {}

            row = start_row + 2
            for day in days:
                ws.write(row, col, day, data_format)

                day_key = [k for k, v in day_map.items() if v == day][0]
                day_subjects = subjects.filter(day=day_key)

                for sub in day_subjects:

                    # Staff list
                    entries = TimetableEntry.objects.filter(subject=sub, user_id=request.user)
                    staff_names = ",".join(staff_abbr.get(e.staff.name, e.staff.name) for e in entries)
                    details = f"{sub.subject_name} ({sub.class_name})\n{staff_names}"

                    # Hours adjust
                    ah = []
                    for h in sub.allotted_hours.split(','):
                        ah.append({'8': '5', '5': '6', '6': '7', '7': '8'}.get(h, h))

                    ah = sorted(set(ah), key=lambda x: int(x))
                    s = int(ah[0]) - 1
                    e = int(ah[-1]) - 1

                    merge_key = (col+1+s, col+1+e)

                    # Check overlap
                    overlaps = False
                    if row in merged_cells:
                        for (ms, me) in merged_cells[row]:
                            if not (merge_key[1] < ms or merge_key[0] > me):
                                overlaps = True
                                break

                    # Write either merged or single cells
                    if not overlaps:
                        ws.merge_range(row, col+1+s, row, col+1+e, details, merge_format)
                        merged_cells.setdefault(row, []).append((merge_key[0], merge_key[1]))
                    else:
                        for h in range(s, e+1):
                            ws.write(row, col+1+h, details, merge_format)

                row += 1

            # Next lab block
            col_offset += len(hours) + 3

        start_row += 12

    workbook.close()
    response = HttpResponse(output.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename="labs_combined.xlsx"'
    return response


    
    
    
#lab wise csv file
import csv
@login_required(login_url='/')
def export_lab_allotments_csv(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="lab_allotments.csv"'

    writer = csv.writer(response)
    
    writer.writerow(['Lab Name', 'Day Allotted', 'Hours Allotted',
                     'Subject Name', 'Class Name', 'Start Date', 'End Date'])

    start_date = '01-06-2025'
    end_date   = '31-12-2026'

    # ✅ Export ALL subject entries – not timetable entries
    subjects = SubjectEntry.objects.filter(period=DP)

    for subject in subjects:
        writer.writerow([
            subject.LAB,
            subject.get_day_display(),
            subject.allotted_hours,
            subject.subject_name,
            subject.class_name,
            start_date,
            end_date
        ])
    
    return response



from django.shortcuts import render, redirect
from .forms import DeleteSubjectEntryForm
@login_required(login_url='/')
def delete_subject_entry_view(request):
    if request.method == 'POST':
        form = DeleteSubjectEntryForm(request.POST)
        if form.is_valid():
            form.delete_entry()
            return HttpResponse('sucess')  # Replace with your desired URL
    else:
        form = DeleteSubjectEntryForm()
    return render(request, 'delete_subject_entry.html', {'form': form})


#google signin
import requests
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from django.shortcuts import redirect
import json

GOOGLE_CLIENT_ID = "84125902506-9jqucnbkpegphqn5ku1g63au6l9hchiv.apps.googleusercontent.com"

@csrf_exempt
def google_auth_callback(request):
    print("haiiiiiiiiiiii")
    if request.method == "POST":
        data = json.loads(request.body)
        token = data.get("id_token")

        # Verify token with Google
        verify_url = f"https://oauth2.googleapis.com/tokeninfo?id_token={token}"
        response = requests.get(verify_url)

        if response.status_code == 200:
            user_info = response.json()
            if user_info["aud"] != GOOGLE_CLIENT_ID:
                return JsonResponse({"error": "Invalid client ID"}, status=400)

            # Save user info in session
            request.session["user_email"] = user_info["email"]
            request.session["user_name"] = user_info.get("name", "")
            return JsonResponse({"redirect": "/allot/"})  # your dashboard or landing page
        else:
            return JsonResponse({"error": "Invalid token"}, status=400)
    return JsonResponse({"error": "Only POST allowed"}, status=405)



#signinpage
def show_google_login_page(request):
    return render(request, 'google_login.html')


from django.contrib.auth.decorators import login_required
from django.shortcuts import render

@login_required
def home(request):
    return render(request, 'home.html')


def logout_view(request):
    logout(request)
    return redirect('login')
    
    
@login_required
def dashboard_view(request):
    return render(request, 'dashboard.html')

    


@login_required
def get_allotments_by_staff(request):
    staff_id = request.GET.get('staff_id')
    data = []

    if staff_id:
        entries = TimetableEntry.objects.filter(staff_id=staff_id).select_related('subject')
        data = [{
            'id': entry.id,
            'label': f"{entry.staff.name} - {entry.subject.subject_name} - {entry.subject.class_name} ({entry.subject.get_day_display()})"
        } for entry in entries]

    return JsonResponse(data, safe=False)

@login_required
def quick_allocate(request, subject_id, staff_id):
    subject = SubjectEntry.objects.get(id=subject_id)
    staff = Staff.objects.get(id=staff_id)

    TimetableEntry.objects.create(
        staff=staff,
        subject=subject,
        user=request.user
    )

    return redirect("allotted")

@login_required
def quick_delete_staff(request, staff_id, subject_id):
    TimetableEntry.objects.filter(
        staff_id=staff_id,
        subject_id=subject_id
    ).delete()

    return redirect("allotted")

#edit staff config
import json,os
from django.conf import settings
from django.contrib.auth.decorators import login_required

CONFIG_FILE = os.path.join(settings.BASE_DIR, "fisat", "config", "staff_config.json")

@login_required
def edit_staff_config(request):
    # Read file
    with open(CONFIG_FILE, "r") as f:
        data = f.read()

    if request.method == "POST":
        new_json = request.POST.get("content")

        # Validate JSON
        try:
            json.loads(new_json)
            with open(CONFIG_FILE, "w") as f:
                f.write(new_json)
            message = "Configuration saved successfully!"
        except:
            message = "Invalid JSON format!"

        return render(request, "staff_config.html", {
            "content": new_json,
            "message": message
        })

    return render(request, "staff_config.html", {"content": data})
    
    
#ai push to database
from django.contrib import messages
from django.shortcuts import redirect
from .models import TimetableEntry, Staff, SubjectEntry

@login_required
def apply_ai_allocation(request):

    # 1️⃣ DELETE ALL OLD TIMETABLE ENTRIES FOR THIS USER
    deleted_count, _ = TimetableEntry.objects.filter(user=request.user).delete()
    print("Deleted old entries:", deleted_count)

    # 2️⃣ REBUILD STAFF LIST BASED ON SENIORITY
    staff_all = list(Staff.objects.all())
    staff_list = sorted(
        staff_all,
        key=lambda s: SENIORITY_ORDER.index(s.name.strip())
    )

    # 3️⃣ LOAD SUBJECTS FOR ALLOCATION
    subjects = list(
        SubjectEntry.objects.filter(period=DP)
        .order_by("class_name", "LAB", "day")
    )

    # 4️⃣ INITIALIZE TRACKERS
    staff_avail = {s.id: {'M': [], 'T': [], 'W': [], 'Th': [], 'F': []} for s in staff_list}
    staff_stats = {
        s.id: {
            "hours": 0,
            "subject_slots": {},
            "batch_counts": {}
        } for s in staff_list
    }

    final_entries = []   # will hold (staff, subject)

    # 5️⃣ RUN AI ENGINE (same logic as timetable2)
    for sub in subjects:
        selected = select_staff(sub, staff_list, staff_avail, staff_stats)
        if not selected:
            continue

        hours = [int(x) for x in sub.allotted_hours.split(',')]
        pmin, pmax = adjusted_range(hours)
        slot_count = (pmax - pmin + 1)

        subj = sub.subject_name.upper()
        batch_key = f"{subj}__{sub.class_name}"

        for staff in selected:
            sid = staff.id

            staff_avail[sid][sub.day].append((pmin, pmax))
            staff_stats[sid]["hours"] += slot_count
            staff_stats[sid]["subject_slots"][subj] = staff_stats[sid]["subject_slots"].get(subj, 0) + slot_count
            staff_stats[sid]["batch_counts"][batch_key] = staff_stats[sid]["batch_counts"].get(batch_key, 0) + 1

            final_entries.append((staff, sub))

    # 6️⃣ BULK INSERT WITHOUT VALIDATIONS
    entries_to_insert = [
        TimetableEntry(
            staff=staff,
            subject=sub,
            user=request.user
        )
        for staff, sub in final_entries
    ]

    TimetableEntry.objects.bulk_create(entries_to_insert, ignore_conflicts=True)

    messages.success(request, "AI allocation successfully applied!")
    return redirect("timetable_auto")

    


#ai allot
import json, os
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from .models import Staff, SubjectEntry

DP = settings.DP

# ------------------------------------------------------------
# LOAD JSON RULE FILE
# ------------------------------------------------------------
RULE_FILE = os.path.join(settings.BASE_DIR, "fisat", "config", "staff_config.json")

with open(RULE_FILE) as f:
    RULES = json.load(f)

SENIORITY_ORDER       = RULES["SENIORITY_ORDER"]
STAFF_PREF            = RULES["PREFERENCES"]
MAX_WORKLOAD          = RULES["WORKLOAD"]
SUBJECT_RULES         = RULES["SUBJECT_RULES"]
MAX_SUBJECT_ALLOTMENT = RULES["MAX_SUBJECT_ALLOTMENT"]
SAME_BATCH_PREF       = RULES["SAME_BATCH_PREF"]
COMMON_SUBJECTS       = set(RULES["COMMON_SUBJECTS"])

DEFAULT_MAX_WORKLOAD  = 22

# 🔹 Subject priority: OS first, then DBMS, then others
SUBJECT_PRIORITY = {
    "OS": 1,
    "DBMS": 2,
    "CASE": 3,
    "NW": 4,
    "C": 5,
    "IT": 6,
    "COA": 7,
    "MP": 8
}

# ------------------------------------------------------------
# TIME HELPERS
# ------------------------------------------------------------
def adjusted_hour(h):
    h = str(h)
    return {
        '8': 5,
        '5': 6,
        '6': 7,
        '7': 8
    }.get(h, int(h))

def adjusted_range(hours):
    adj = [adjusted_hour(h) for h in hours]
    return min(adj), max(adj)

def intervals_overlap(a1, a2, b1, b2):
    return not (a2 < b1 or a1 > b2)


# ------------------------------------------------------------
# CAN ASSIGN STAFF
#   - time clash
#   - workload
#   - MAX_SUBJECT_ALLOTMENT = max number of entries (batches) for that subject
# ------------------------------------------------------------
def can_assign(staff_stats, staff_avail, staff, day, pmin, pmax, subj, cls):
    sid = staff.id

    # 1) TIME CONFLICT
    for (a, b) in staff_avail[sid][day]:
        if intervals_overlap(a, b, pmin, pmax):
            return False

    # 2) WORKLOAD
    hours = (pmax - pmin + 1)
    max_hours = MAX_WORKLOAD.get(staff.name, DEFAULT_MAX_WORKLOAD)
    if staff_stats[sid]["hours"] + hours > max_hours:
        return False

    # 3) SUBJECT ENTRY LIMIT (batches, not hours)
    #    subject_slots[subj] = number of SubjectEntry rows already given to this staff
    used_entries = staff_stats[sid]["subject_slots"].get(subj, 0)
    limit = MAX_SUBJECT_ALLOTMENT.get(subj, 999)

    if used_entries + 1 > limit:
        return False

    return True


# ------------------------------------------------------------
# SELECT STAFF FOR SUBJECT
# ------------------------------------------------------------
def select_staff(subject, staff_list, staff_avail, staff_stats):
    subj = subject.subject_name.upper()
    cls  = subject.class_name
    key_batch = f"{subj}__{cls}"

    hours = [int(x) for x in subject.allotted_hours.split(',')]
    pmin, pmax = adjusted_range(hours)
    day = subject.day

    required = SUBJECT_RULES.get(subj, 1)  # min staff needed
    selected = []

    def pick(staff):
        return can_assign(staff_stats, staff_avail, staff, day, pmin, pmax, subj, cls)

    # 0️⃣ SAME BATCH FIRST – if someone already handles this (subj, class),
    #    we try to keep it with same staff
    for s in staff_list:
        if len(selected) >= required:
            break
        sid = s.id
        if staff_stats[sid]["batch_counts"].get(key_batch, 0) > 0:
            if pick(s):
                selected.append(s)

    if len(selected) == required:
        return selected

    # 1️⃣ FIRST PREFERENCE (seniority respected)
    for s in staff_list:
        if len(selected) >= required:
            break
        prefs = STAFF_PREF.get(s.name, ["", ""])
        if prefs and prefs[0].upper() == subj and pick(s):
            selected.append(s)

    if len(selected) == required:
        return selected

    # 2️⃣ SECOND PREFERENCE
    for s in staff_list:
        if len(selected) >= required:
            break
        prefs = STAFF_PREF.get(s.name, ["", ""])
        if len(prefs) > 1 and prefs[1].upper() == subj and pick(s):
            selected.append(s)

    if len(selected) == required:
        return selected

    # 3️⃣ COMMON SUBJECTS (C, IT, COA) – any senior free staff
    if subj in COMMON_SUBJECTS:
        for s in staff_list:
            if len(selected) >= required:
                break
            if pick(s):
                selected.append(s)

        if len(selected) == required:
            return selected

    # 4️⃣ FALLBACK → ANY ELIGIBLE STAFF
    for s in staff_list:
        if len(selected) >= required:
            break
        if pick(s):
            selected.append(s)

    return selected


# ------------------------------------------------------------
# FINAL AI TIMETABLE
# ------------------------------------------------------------
@login_required
def timetable2(request):

    # ORDER STAFF BY SENIORITY
    staff_all = list(Staff.objects.all())
    staff_list = sorted(
        staff_all,
        key=lambda s: SENIORITY_ORDER.index(s.name.strip())
                      if s.name.strip() in SENIORITY_ORDER else 999
    )

    # LOAD SUBJECTS FOR THIS PERIOD
    subjects = list(SubjectEntry.objects.filter(period=DP))

    # Sort subjects so OS first, then DBMS, then others
    def subj_sort_key(sub):
        sname = sub.subject_name.upper()
        return (
            SUBJECT_PRIORITY.get(sname, 99),
            sub.class_name,
            sub.LAB,
            sub.day
        )

    subjects.sort(key=subj_sort_key)

    # INIT
    staff_avail = {s.id: {'M': [], 'T': [], 'W': [], 'Th': [], 'F': []} for s in staff_list}
    staff_stats = {
        s.id: {
            "hours": 0,
            "subject_slots": {},   # subj -> number of entries assigned
            "batch_counts": {}     # subj__class -> rows count
        } for s in staff_list
    }
    assigned_map = {s.id: [] for s in staff_list}

    # ASSIGN SUBJECTS
    for sub in subjects:
        selected = select_staff(sub, staff_list, staff_avail, staff_stats)
        if not selected:
            continue

        hours = [int(x) for x in sub.allotted_hours.split(',')]
        pmin, pmax = adjusted_range(hours)
        slot_count = (pmax - pmin + 1)
        subj = sub.subject_name.upper()
        key = f"{subj}__{sub.class_name}"

        for s in selected:
            sid = s.id

            # 1) time occupied
            staff_avail[sid][sub.day].append((pmin, pmax))

            # 2) workload
            staff_stats[sid]["hours"] += slot_count

            # 3) subject_slots: count how many DBMS/OS/etc entries staff has
            staff_stats[sid]["subject_slots"][subj] = \
                staff_stats[sid]["subject_slots"].get(subj, 0) + 1

            # 4) same-batch counter
            staff_stats[sid]["batch_counts"][key] = \
                staff_stats[sid]["batch_counts"].get(key, 0) + 1

            # 5) save assignment
            assigned_map[sid].append(sub)

    # BUILD TIMETABLE FOR TEMPLATE
    staff_timetables = {}
    for s in staff_list:
        slots = [["" for _ in range(8)] for _ in range(5)]
        workload = staff_stats[s.id]["hours"]

        for sub in assigned_map[s.id]:
            hours = [int(x) for x in sub.allotted_hours.split(',')]
            row = {"M": 0, "T": 1, "W": 2, "Th": 3, "F": 4}[sub.day]

            adj = sorted(set(adjusted_hour(h) for h in hours))
            start = adj[0] - 1
            end = adj[-1] - 1

            for c in range(start, end + 1):
                if c == start:
                    slots[row][c] = {
                        "subject": sub.subject_name,
                        "class_name": sub.class_name,
                        "lab": sub.LAB,
                        "colspan": end - start + 1
                    }
                else:
                    slots[row][c] = None

        staff_timetables[s.name] = {
            "timetable_slots": slots,
            "total_hour": workload
        }

    return render(request, "timetable_auto.html", {
        "staff_timetables": staff_timetables
    })

