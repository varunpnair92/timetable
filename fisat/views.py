from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.db import IntegrityError, transaction

import csv
import io
import json
import os
import requests

from .forms import (
    AllocationForm,
    SubjectEntryForm,
    DeleteSubjectEntryForm,
)
from .models import (
    Staff,
    SubjectEntry,
    TimetableEntry,
    Batch,
    BatchSubject,
    Semester
)
from django.http import JsonResponse

def get_current_period(request=None):
    if request and 'selected_period' in request.session:
        return request.session['selected_period']
    try:
        active_sem = Semester.objects.get(is_active=True)
        return active_sem.name
    except Semester.DoesNotExist:
        return settings.DP

DEFAULT_DP = settings.DP

# ============================================================
#  JSON CONFIG – DYNAMIC LOAD
# ============================================================

CONFIG_FILE = os.path.join(settings.BASE_DIR, "fisat", "config", "staff_config.json")
RULE_FILE = CONFIG_FILE  # alias


def load_rules():
    """Load JSON rules fresh from file each time."""
    with open(RULE_FILE) as f:
        return json.load(f)


# Initial load (will be overridden by timetable2/apply_ai_allocation)
RULES = load_rules()

SENIORITY_ORDER = RULES["SENIORITY_ORDER"]
STAFF_PREF = RULES["PREFERENCES"]
MAX_WORKLOAD = RULES["WORKLOAD"]
SUBJECT_RULES = RULES["SUBJECT_RULES"]
MAX_SUBJECT_ALLOTMENT = RULES["MAX_SUBJECT_ALLOTMENT"]
SAME_BATCH_PREF = RULES["SAME_BATCH_PREF"]
COMMON_SUBJECTS = set(RULES["COMMON_SUBJECTS"])
DEFAULT_MAX_WORKLOAD = 22

# Subject priority: which subject AI should allocate first
SUBJECT_PRIORITY = {
    "OS": 1,
    "DBMS": 2,
    "CASE": 3,
    "NW": 4,
    "C": 5,
    "IT": 6,
    "COA": 7,
    "MP": 8,
}

# ============================================================
#  AI HELPER FUNCTIONS
# ============================================================


def adjusted_hour(h):
    h = str(h)
    return {
        "8": 5,
        "5": 6,
        "6": 7,
        "7": 8,
    }.get(h, int(h))


def adjusted_range(hours):
    adj = [adjusted_hour(h) for h in hours]
    return min(adj), max(adj)


def intervals_overlap(a1, a2, b1, b2):
    return not (a2 < b1 or a1 > b2)


def can_assign(staff_stats, staff_avail, staff, day, pmin, pmax, subj, cls):
    """
    Check if given staff can be assigned this SubjectEntry.
    Uses global RULES variables (updated dynamically in timetable2 / apply_ai_allocation).
    """
    sid = staff.id

    # 1) Time conflict
    for (a, b) in staff_avail[sid][day]:
        if intervals_overlap(a, b, pmin, pmax):
            return False

    # 2) Workload check
    hours = pmax - pmin + 1
    max_hours = MAX_WORKLOAD.get(staff.name, DEFAULT_MAX_WORKLOAD)
    if staff_stats[sid]["hours"] + hours > max_hours:
        return False

    # 3) Subject entry limit (batches, not hours)
    used_entries = staff_stats[sid]["subject_slots"].get(subj, 0)
    limit = MAX_SUBJECT_ALLOTMENT.get(subj, 999)
    if used_entries + 1 > limit:
        return False

    return True


def select_staff(subject, staff_list, staff_avail, staff_stats):
    """
    Core AI allocation logic for a single SubjectEntry.
    Uses:
      - SAME BATCH first,
      - then 1st preference,
      - then 2nd preference,
      - then common subjects,
      - then any eligible staff (all in seniority order).
    """
    subj = subject.subject_name.upper()
    cls = subject.class_name
    key_batch = f"{subj}__{cls}"

    hours = [int(x) for x in subject.allotted_hours.split(",")]
    pmin, pmax = adjusted_range(hours)
    day = subject.day

    required = SUBJECT_RULES.get(subj, 1)  # how many staff required for this subject row
    selected = []

    def pick(staff):
        return can_assign(staff_stats, staff_avail, staff, day, pmin, pmax, subj, cls)

    # 0️⃣ SAME BATCH FIRST – keep same staff for same (subj, class)
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


# ============================================================
#  ALLOCATE STAFF (MANUAL FORM)
# ============================================================


@login_required(login_url="/")
def allocate_staff(request):
    dp = get_current_period(request)
    action = (
        request.POST.get("action")
        if request.method == "POST"
        else request.GET.get("action", "allot")
    )

    if request.method == "POST":
        form = AllocationForm(request.POST, action=action, user=request.user, period=dp)
        if form.is_valid():
            if action == "delete":
                delete_entry = form.cleaned_data["delete_entry"]
                if delete_entry:
                    delete_entry.delete()
            elif action == "allot":
                form.save()
            return redirect(reverse("timetable"))
    else:
        form = AllocationForm(action=action, user=request.user, period=dp)

    return render(request, "allocate.html", {"form": form, "action": action})


# ============================================================
#  STAFF TIMETABLE VIEW (FROM TimetableEntry TABLE)
# ============================================================


@login_required(login_url="/")
def timetable(request):
    dp = get_current_period(request)
    staff_members = Staff.objects.all()
    staff_timetables = {}

    for staff in staff_members:

        # 5 days × 8 hours grid
        timetable_slots = [["" for _ in range(8)] for _ in range(5)]

        # get staff timetable entries for this user + period
        timetable_entries = TimetableEntry.objects.filter(
            staff=staff,
            subject__period=dp,
            user=request.user
        )

        workload = 0

        # Map day letters to row index
        day_to_row = {"M": 0, "T": 1, "W": 2, "Th": 3, "F": 4}

        for entry in timetable_entries:
            subject_entry = entry.subject

            hours = subject_entry.allotted_hours.split(",")
            workload += len(hours)

            day = subject_entry.day
            row_index = day_to_row.get(day, None)
            if row_index is None:
                continue

            # Adjust hours: 8→5, 5→6, 6→7, 7→8
            adjusted_hours = []
            for hour in hours:
                if hour == "8":
                    adjusted_hours.append("5")
                elif hour == "5":
                    adjusted_hours.append("6")
                elif hour == "6":
                    adjusted_hours.append("7")
                elif hour == "7":
                    adjusted_hours.append("8")
                else:
                    adjusted_hours.append(hour)

            adjusted_hours = sorted(set(adjusted_hours), key=lambda x: int(x))

            # Compute column start/end
            start_index = int(adjusted_hours[0]) - 1
            end_index = int(adjusted_hours[-1]) - 1

            if start_index < 0: 
                start_index = 0
            if end_index > 7:
                end_index = 7

            # Fill timetable grid
            for col_index in range(start_index, end_index + 1):
                if col_index == start_index:
                    # ⭐ MAIN SLOT – We store entry_id here
                    timetable_slots[row_index][col_index] = {
                        "lab": subject_entry.LAB,
                        "class_name": subject_entry.class_name,
                        "subject": subject_entry.subject_name,
                        "entry_id": entry.id,     # ⭐ IMPORTANT
                        "colspan": end_index - start_index + 1,
                    }
                else:
                    # Fill skipped cells with None
                    timetable_slots[row_index][col_index] = None

        staff_timetables[staff.name] = {
            "timetable_slots": timetable_slots,
            "total_hour": workload,
            "staff_id": staff.id,
        }

    has_undo = bool(request.session.get('undo_data'))

    # Build Palette Subjects
    all_subjects = SubjectEntry.objects.filter(period=dp)
    palette_subjects = {}
    for sub in all_subjects:
        if sub.class_name not in palette_subjects:
            palette_subjects[sub.class_name] = []
        
        # Replace '8' with 'LB' for display in the palette
        display_hours = sub.allotted_hours.replace('8', 'LB') if sub.allotted_hours else ''
        
        palette_subjects[sub.class_name].append({
            'id': sub.id,
            'subject_name': sub.subject_name,
            'day': sub.day,
            'allotted_hours': display_hours,
            'lab': sub.LAB
        })

    semesters = Semester.objects.all().order_by('name')
    return render(request, "timetable.html", {
        "staff_timetables": staff_timetables, 
        "has_undo": has_undo,
        "palette_subjects": palette_subjects,
        "semesters": semesters,
        "current_view_sem": dp
    })

# ============================================================
#  CLASS WISE ALLOTTED VIEW
# ============================================================


@login_required(login_url="/")
def allotted(request):
    subjects = SubjectEntry.objects.filter(period=dp)
    staff_list = Staff.objects.all()

    class_data = {}

    for subject in subjects:
        class_name = subject.class_name

        if class_name not in class_data:
            class_data[class_name] = []

        allocations = TimetableEntry.objects.filter(
            subject=subject, subject__period=dp
        )
        allocated_staff = [a.staff for a in allocations]

        class_data[class_name].append(
            {
                "subject_id": subject.id,
                "subject_name": subject.subject_name,
                "allocated_staff": allocated_staff,
                "all_staff": staff_list,
                "day": subject.day,
                "allotted_hours": subject.allotted_hours,
            }
        )

    return render(request, "allotted.html", {"class_data": class_data})


# ============================================================
#  GET FREE STAFF FOR A SUBJECT (AJAX)
# ============================================================


from django.shortcuts import get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required

# make sure these are imported at top of file
# from .models import SubjectEntry, Staff, TimetableEntry
# from django.conf import settings
# DP = settings.DP

@login_required
def get_free_staff(request, subject_id):
    """
    Returns list of staff free for the subject's slot, plus the number of
    slots that staff already has for the same subject (subject name) in this period.
    JSON: [{id, name, count}, ...]
    """
    subject = get_object_or_404(SubjectEntry, id=subject_id, period=dp)

    # target info
    target_day = subject.day
    # parse hours safely (ignore empty parts)
    try:
        target_hours = set(int(x) for x in subject.allotted_hours.split(',') if x.strip() != '')
    except Exception:
        target_hours = set()

    staff_qs = Staff.objects.all().order_by('name')
    free_staff = []

    for st in staff_qs:
        # all timetable entries for this staff on the same day & period
        entries = TimetableEntry.objects.filter(
            staff=st,
            subject__day=target_day,
            subject__period=dp
        )

        busy = False
        for e in entries:
            try:
                entry_hours = set(int(x) for x in e.subject.allotted_hours.split(',') if x.strip() != '')
            except Exception:
                entry_hours = set()
            if target_hours.intersection(entry_hours):
                busy = True
                break

        if busy:
            continue

        # count how many slots this staff already has for the SAME subject name
        # (case-insensitive match on subject_name) within same period
        same_subject_entries = TimetableEntry.objects.filter(
            staff=st,
            subject__subject_name__iexact=subject.subject_name,
            subject__period=dp
        )

        # count total hours/slots for those entries (we count "slots" as the number of hours)
        slot_count = 0
        for ent in same_subject_entries:
            try:
                slot_count += sum(1 for x in ent.subject.allotted_hours.split(',') if x.strip() != '')
            except Exception:
                pass

        free_staff.append({
            "id": st.id,
            "name": st.name,
            "count": slot_count
        })

    return JsonResponse(free_staff, safe=False)

# ============================================================
#  DELETE A SINGLE TIMETABLE ENTRY
# ============================================================


@login_required(login_url="/")
def delete_allotment(request, entry_id):
    entry = get_object_or_404(TimetableEntry, id=entry_id)
    entry.delete()
    return redirect("timetable")


@login_required(login_url="/")
def drag_action(request, action, id1, id2):
    entry1 = get_object_or_404(TimetableEntry, id=id1)
    entry2 = get_object_or_404(TimetableEntry, id=id2)
    
    staff1 = entry1.staff
    staff2 = entry2.staff

    try:
        with transaction.atomic():
            if action == 'swap':
                TimetableEntry.objects.filter(id=id1).update(staff=staff2)
                TimetableEntry.objects.filter(id=id2).update(staff=staff1)
                request.session['undo_data'] = {
                    'type': 'swap',
                    'id1': id1,
                    'id2': id2
                }
            elif action == 'allot':
                deleted_entry_data = {
                    'staff_id': staff2.id,
                    'subject_id': entry2.subject.id,
                }
                request.session['undo_data'] = {
                    'type': 'allot',
                    'id1': id1,
                    'old_staff1_id': staff1.id,
                    'deleted_entry': deleted_entry_data
                }
                TimetableEntry.objects.filter(id=id1).update(staff=staff2)
                entry2.delete()
    except IntegrityError:
        messages.error(request, "Action failed: This staff member is already assigned to this subject.")
        
    return redirect("timetable")


@login_required(login_url="/")
def transfer_to_staff(request, entry_id, staff_id):
    entry = get_object_or_404(TimetableEntry, id=entry_id)
    old_staff = entry.staff
    new_staff = get_object_or_404(Staff, id=staff_id)
    
    try:
        with transaction.atomic():
            TimetableEntry.objects.filter(id=entry_id).update(staff=new_staff)
            
            request.session['undo_data'] = {
                'type': 'transfer',
                'entry_id': entry_id,
                'old_staff_id': old_staff.id
            }
    except IntegrityError:
        messages.error(request, "Action failed: This staff member is already assigned to this subject.")
    return redirect("timetable")


@login_required(login_url="/")
def undo_last_action(request):
    undo_data = request.session.pop('undo_data', None)
    if not undo_data:
        return redirect("timetable")
        
    action_type = undo_data.get('type')
    
    try:
        with transaction.atomic():
            if action_type == 'swap':
                id1 = undo_data['id1']
                id2 = undo_data['id2']
                entry1 = TimetableEntry.objects.filter(id=id1).first()
                entry2 = TimetableEntry.objects.filter(id=id2).first()
                if entry1 and entry2:
                    staff1 = entry1.staff
                    staff2 = entry2.staff
                    TimetableEntry.objects.filter(id=id1).update(staff=staff2)
                    TimetableEntry.objects.filter(id=id2).update(staff=staff1)
                    
            elif action_type == 'allot':
                id1 = undo_data['id1']
                old_staff1_id = undo_data['old_staff1_id']
                deleted_entry = undo_data['deleted_entry']
                
                old_staff = Staff.objects.filter(id=old_staff1_id).first()
                if old_staff:
                    TimetableEntry.objects.filter(id=id1).update(staff=old_staff)
                    
                staff2 = Staff.objects.filter(id=deleted_entry['staff_id']).first()
                subject = SubjectEntry.objects.filter(id=deleted_entry['subject_id']).first()
                
                if staff2 and subject:
                    TimetableEntry.objects.create(
                        staff=staff2,
                        subject=subject,
                        user=request.user
                    )
                    
            elif action_type == 'transfer':
                entry_id = undo_data['entry_id']
                old_staff_id = undo_data['old_staff_id']
                old_staff = Staff.objects.filter(id=old_staff_id).first()
                if old_staff:
                    TimetableEntry.objects.filter(id=entry_id).update(staff=old_staff)
    except IntegrityError:
        messages.error(request, "Undo failed due to a database constraint.")
            
    return redirect("timetable")


# ============================================================
#  SUBJECT ENTRY FORM
# ============================================================


@login_required(login_url="")
def allot_subject_entry(request):
    if request.method == "POST":
        form = SubjectEntryForm(request.POST)
        if form.is_valid():
            form.save()
            return HttpResponse("Success! Subject entries have been allotted.")
    else:
        form = SubjectEntryForm()
    return render(request, "subject.html", {"form": form})


# ============================================================
#  LAB WISE EXCEL (ONE LAB PER SHEET)
# ============================================================


@login_required(login_url="/")
def timetableexcel(request):
    dp = get_current_period(request)
    import xlsxwriter
    from .models import SubjectFacultyMap

    logo_path = "/home/varun/fisatlab/static/fisat_logo.png"

    labs = (
        SubjectEntry.objects.filter(period=dp)
        .values_list("LAB", flat=True)
        .distinct()
    )

    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output)

    # ======= FORMATS =======
    institute_fmt = workbook.add_format({
        "bold": True, "font_size": 20,
        "align": "center", "valign": "vcenter"
    })
    address_fmt = workbook.add_format({
        "font_size": 14, "align": "center", "valign": "vcenter"
    })
    title_fmt = workbook.add_format({
        "bold": True, "font_size": 16,
        "align": "center", "valign": "vcenter"
    })

    # ⭐ LAB NAME HEADER FORMAT
    lab_header_fmt = workbook.add_format({
        "bold": True, "font_size": 15,
        "align": "center", "valign": "vcenter",
        "bg_color": "#d9ead3", "border": 1
    })

    header_fmt = workbook.add_format({
        "bold": True, "align": "center", "valign": "vcenter",
        "bg_color": "#F2F2F2", "border": 1
    })
    data_fmt = workbook.add_format({"align": "center", "valign": "vcenter", "border": 1})
    merge_fmt = workbook.add_format({"align": "center", "valign": "vcenter", "border": 1})
    empty_fmt = workbook.add_format({"bg_color": "#D3D3D3", "border": 1})

    staff_abbr = {
        "AMBILY N MENON": "ANM", "SREELALITHAMBIKA P K": "SL",
        "SANDYA O C": "SOC", "NEEBA CHERIYACHAN": "NC",
        "NOMA MATHEW": "NM", "AMBILY SEKAR C": "AS",
        "VARUN P NAIR": "VPN", "ARAVIND BALAN": "AB",
        "SALINI T R": "STR", "SMIJA M B": "SM", "JOYCY": "JY",
    }

    days = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    hours = ["H1", "H2", "H3", "H4", "LB", "H5", "H6", "H7"]
    day_map = {"M":"Mon","T":"Tue","W":"Wed","Th":"Thu","F":"Fri"}

    for lab in labs:
        ws = workbook.add_worksheet(lab)
        ws.set_paper(9)                # A4
        ws.set_landscape()             # Landscape orientation
        ws.center_horizontally()
        ws.center_vertically()
        ws.fit_to_pages(1, 1)          # Fit on one A4 sheet
        ws.set_margins(left=0.3, right=0.3, top=0.5, bottom=0.5)

        # ========== LOGO ==========
        try:
            ws.insert_image("A1", logo_path, {"x_scale": 0.3, "y_scale": 0.3})
        except:
            pass

        # ========== INSTITUTE HEADER ==========
        ws.merge_range("A1:I1",
                       """FEDERAL INSTITUTE OF SCIENCE AND TECHNOLOGY (FISAT)
                        (Hormis Nagar, Mookkannoor, Angamaly, Kerala – 683577)
                        LAB TIMETABLE FOR B.TECH (DEC 2025 – MAY 2025)""",institute_fmt)

        '''ws.merge_range("B2:I2",
                       "(Hormis Nagar, Mookkannoor, Angamaly, Kerala – 683577)",
                       address_fmt)

        #ws.merge_range("B3:I3",
                       "LAB TIMETABLE FOR B.TECH (DEC 2025 – MAY 2025)",
                       title_fmt)'''

        # ⭐⭐⭐ LAB NAME HEADER MERGED ABOVE HOURS ⭐⭐⭐
        ws.merge_range("A4:I4", f"CCF : {lab}", lab_header_fmt)

        # ========== TABLE HEADER ==========
        ws.write(4, 0, "Day", header_fmt)
        for c, h in enumerate(hours):
            ws.write(4, c + 1, h, header_fmt)

        row = 5
        subjects = SubjectEntry.objects.filter(LAB=lab, period=dp).order_by("day")
        merged_cells = {}

        for day in days:
            ws.write(row, 0, day, data_fmt)
            key = [k for k, v in day_map.items() if v == day][0]
            subs = subjects.filter(day=key)

            for sub in subs:
                entries = TimetableEntry.objects.filter(subject=sub, user=request.user)
                staff_names = ",".join(
                    staff_abbr.get(e.staff.name, e.staff.name) for e in entries
                ) or "—"

                try:
                    fac = SubjectFacultyMap.objects.get(subject=sub)
                    faculty = fac.faculty_names or "—"
                except:
                    faculty = "—"

                text = f"{sub.subject_name} ({sub.class_name})\n({faculty}) ({staff_names})"

                adj = {"8": "5", "5": "6", "6": "7", "7": "8"}
                ah = sorted({int(adj.get(h, h)) for h in sub.allotted_hours.split(",")})

                s = ah[0] - 1
                e = ah[-1] - 1

                if row not in merged_cells:
                    merged_cells[row] = []

                overlap = any(ms <= s + 1 <= me or ms <= e + 1 <= me for (ms, me) in merged_cells[row])

                if not overlap:
                    ws.merge_range(row, s + 1, row, e + 1, text, merge_fmt)
                    merged_cells[row].append((s + 1, e + 1))
                else:
                    for col in range(s + 1, e + 2):
                        ws.write(row, col, text, merge_fmt)

            for col in range(1, 9):
                if not any(ms <= col <= me for (ms, me) in merged_cells.get(row, [])):
                    ws.write(row, col, "", empty_fmt)

            row += 1

        ws.set_column("A:A", 7)
        ws.set_column("B:I", 11)
        ws.set_default_row(45)

    workbook.close()
    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = 'attachment; filename="lab_details.xlsx"'
    return response


# ============================================================
#  COMBINED LABS EXCEL
# ============================================================


@login_required(login_url="/")
def timetableexcel_combined(request):
    import xlsxwriter
    from .models import SubjectFacultyMap

    logo_path = "/home/varun/fisatlab/static/fisat_logo.png"

    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output)
    ws = workbook.add_worksheet("Combined Labs")

    # ===== FORMATS =====
    institute_fmt = workbook.add_format({"bold": True, "font_size": 20,
                                         "align": "center", "valign": "vcenter"})
    address_fmt = workbook.add_format({"font_size": 14,
                                       "align": "center", "valign": "vcenter"})
    title_fmt = workbook.add_format({"bold": True, "font_size": 16,
                                     "align": "center", "valign": "vcenter"})

    header_fmt = workbook.add_format({"bold": True, "align": "center",
                                      "bg_color": "#F2F2F2", "border": 1})
    data_fmt = workbook.add_format({"align": "center", "border": 1})
    merge_fmt = workbook.add_format({"align": "center", "border": 1})
    empty_fmt = workbook.add_format({"bg_color": "#D3D3D3", "border": 1})

    staff_abbr = {
        "AMBILY N MENON": "ANM", "SREELALITHAMBIKA P K": "SL",
        "SANDYA O C": "SOC", "NEEBA CHERIYACHAN": "NC",
        "NOMA MATHEW": "NM", "AMBILY SEKAR C": "AS",
        "VARUN P NAIR": "VPN", "ARAVIND BALAN": "AB",
        "SALINI T R": "STR", "SMIJA M B": "SM", "JOYCY": "JY",
    }

    days = ["Mon","Tue","Wed","Thu","Fri"]
    hours = ["H1","H2","H3","H4","LB","H5","H6","H7"]
    day_map = {"M":"Mon","T":"Tue","W":"Wed","Th":"Thu","F":"Fri"}

    lab_groups = [
        ["L1","L2","L3"],
        ["L5","L7","L8"],
        ["L4","L6"],
        ["L9","PG LAB"]
    ]

    # ===== LOGO =====
    try:
        ws.insert_image("A1", logo_path, {"x_scale": 0.3, "y_scale": 0.3})
    except:
        pass

    # ===== SINGLE ROW HEADER =====
    ws.merge_range("A1:AF1",
        "FEDERAL INSTITUTE OF SCIENCE AND TECHNOLOGY (FISAT)",
        institute_fmt)
    ws.merge_range("B2:Z2",
        "(Hormis Nagar, Mookkannoor, Angamaly, Kerala – 683577)",
        address_fmt)
    ws.merge_range("B3:Z3",
        "COMBINED LAB TIMETABLE FOR B.TECH (DEC 2025 – MAY 2025)",
        title_fmt)

    start_row = 4

    for group in lab_groups:
        col_offset = 0

        for lab in group:
            col = col_offset

            ws.write(start_row, col, lab, header_fmt)
            ws.write(start_row+1, col, "Day", header_fmt)

            for i,h in enumerate(hours):
                ws.write(start_row+1, col+1+i, h, header_fmt)

            merged = {}
            row = start_row + 2

            subjects = SubjectEntry.objects.filter(LAB=lab, period=dp).order_by("day")

            for day in days:
                ws.write(row, col, day, data_fmt)

                key = [k for k,v in day_map.items() if v==day][0]
                subs = subjects.filter(day=key)

                for sub in subs:

                    # staff
                    entries = TimetableEntry.objects.filter(subject=sub, user_id=request.user)
                    staff_names = ",".join(staff_abbr.get(e.staff.name,e.staff.name)
                                           for e in entries) or "—"

                    # faculty
                    try:
                        fac = SubjectFacultyMap.objects.get(subject=sub)
                        faculty = fac.faculty_names or "—"
                    except:
                        faculty = "—"

                    text = f"{sub.subject_name} ({sub.class_name})\n({faculty}) ({staff_names})"

                    adj = {"8":"5","5":"6","6":"7","7":"8"}
                    ah = sorted({int(adj.get(h,h)) for h in sub.allotted_hours.split(",")})

                    s = ah[0] - 1
                    e = ah[-1] - 1

                    if row not in merged:
                        merged[row] = []

                    merge_key = (col+1+s, col+1+e)

                    overlap = any(ms<=merge_key[0]<=me or ms<=merge_key[1]<=me
                                  for (ms,me) in merged[row])

                    if not overlap:
                        ws.merge_range(row, col+1+s, row, col+1+e, text, merge_fmt)
                        merged[row].append((col+1+s, col+1+e))
                    else:
                        for c in range(col+1+s, col+2+e):
                            ws.write(row, c, text, merge_fmt)

                # free slots
                for c in range(col+1, col+1+len(hours)):
                    if not any(ms<=c<=me for (ms,me) in merged.get(row,[])):
                        ws.write(row, c, "", empty_fmt)

                row += 1

            col_offset += len(hours) + 3

        start_row += 12

    workbook.close()
    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = 'attachment; filename="labs_combined.xlsx"'
    return response




#download staff entry details
import csv
from django.http import HttpResponse
from django.conf import settings
from .models import SubjectEntry
# DP removed
def download_subject_entries_csv(request):
    # Create the HTTP response for CSV
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="subject_entries_{DP}.csv"'

    writer = csv.writer(response)
    
    # CSV Header
    writer.writerow([
        "ID",
        "Subject Name",
        "Class",
        "Day",
        "Hours",
        "Lab",
        "Period"
    ])

    # Query Subjects only for current period DP
    subjects = SubjectEntry.objects.filter(period=dp).order_by("class_name", "subject_name")

    # Write data rows
    for s in subjects:
        writer.writerow([
            s.id,
            s.subject_name,
            s.class_name,
            s.get_day_display(),
            s.allotted_hours,
            s.LAB,
            s.period
        ])

    return response





# ============================================================
#  LAB ALLOTMENTS CSV (SUBJECT ENTRY EXPORT)
# ============================================================


@login_required(login_url="/")
def export_lab_allotments_csv(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="lab_allotments.csv"'

    writer = csv.writer(response)
    writer.writerow(
        [
            "Lab Name",
            "Day Allotted",
            "Hours Allotted",
            "Subject Name",
            "Class Name",
            "Start Date",
            "End Date",
        ]
    )

    start_date = "01-06-2025"
    end_date = "31-12-2026"

    subjects = SubjectEntry.objects.filter(period=dp)

    for subject in subjects:
        writer.writerow(
            [
                subject.LAB,
                subject.get_day_display(),
                subject.allotted_hours,
                subject.subject_name,
                subject.class_name,
                start_date,
                end_date,
            ]
        )

    return response
#exportstaff allotment
import csv
from django.http import HttpResponse
from .models import TimetableEntry
from django.conf import settings
# DP removed
def download_staff_allotment_csv(request):
    dp = get_current_period(request)
    # Response settings
    response = HttpResponse(content_type="text/csv")
    response['Content-Disposition'] = f'attachment; filename="staff_allotment_{dp}.csv"'

    writer = csv.writer(response)
    writer.writerow(["Staff Name", "Subject", "Class", "Lab", "Day", "Hours"])

    entries = (
        TimetableEntry.objects
        .filter(subject__period=dp)
        .select_related("staff", "subject")
        .order_by("staff__name", "subject__subject_name")
    )

    for e in entries:
        writer.writerow([
            e.staff.name,
            e.subject.subject_name,
            e.subject.class_name,
            e.subject.LAB,
            e.subject.get_day_display(),
            e.subject.allotted_hours
        ])

    return response

#its download select * fro timetable for backup
import csv
from django.http import HttpResponse
from .models import TimetableEntry

def download_timetable_csv(request):
    # Create HTTP response with CSV content
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="timetableentry_dump.csv"'

    writer = csv.writer(response)

    # Header row (same as database columns)
    writer.writerow(["tid", "staffid", "subjectid", "user_id"])

    # SELECT * FROM timetableentry
    for row in TimetableEntry.objects.all().order_by("id"):
        writer.writerow([
            row.id,
            row.staff_id,
            row.subject_id,
            row.user_id,
        ])

    return response


#download timetable each staff allotment image
'''
# views.py
from django.http import HttpResponse
from django.conf import settings
from django.shortcuts import render
from django.utils.text import slugify
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
import zipfile
from .models import Staff, TimetableEntry


def generate_staff_image(staff_name, timetable_text):

    img = Image.new("RGB", (1400, 1800), "white")
    draw = ImageDraw.Draw(img)

    # --------- USE DEFAULT FONT (WORKS ON ALL SERVERS) ----------
    title_font = ImageFont.load_default()
    text_font = ImageFont.load_default()

    # Fake bold by drawing multiple times slightly offset
    def draw_bold_text(x, y, text, font):
        draw.text((x, y), text, fill="black", font=font)
        draw.text((x+1, y), text, fill="black", font=font)
        draw.text((x, y+1), text, fill="black", font=font)
        draw.text((x+1, y+1), text, fill="black", font=font)

    # --------- TITLE ---------
    draw_bold_text(50, 40, f"Staff Timetable - {staff_name}", title_font)

    # --------- BODY TEXT ---------
    y = 150
    for line in timetable_text.split("\n"):
        draw.text((50, y), line, fill="black", font=text_font)
        y += 40

    return img


def download_all_staff_jpegs(request):
    buffer = BytesIO()
    zip_file = zipfile.ZipFile(buffer, "w")

    staff_list = Staff.objects.all().order_by("name")

    for staff in staff_list:
        entries = TimetableEntry.objects.filter(staff=staff).select_related("subject")

        text_lines = []
        for e in entries:
            text_lines.append(
                f"{e.subject.subject_name} | {e.subject.class_name} | "
                f"{e.subject.get_day_display()} | Hours: {e.subject.allotted_hours} | Lab: {e.subject.LAB}"
            )

        timetable_text = "\n".join(text_lines) if text_lines else "No allotments."

        image = generate_staff_image(staff.name, timetable_text)

        img_bytes = BytesIO()
        image.save(img_bytes, format="JPEG")
        img_bytes.seek(0)

        filename = f"{slugify(staff.name)}.jpg"
        zip_file.writestr(filename, img_bytes.getvalue())

    zip_file.close()

    response = HttpResponse(buffer.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = 'attachment; filename=\"staff_timetables.zip\"'
    return response
'''

#subject wise view
from django.shortcuts import render
from django.db.models import Prefetch
from collections import OrderedDict
from .models import SubjectEntry, TimetableEntry, Staff
from django.conf import settings
# DP removed
# Custom sorting for class names
def class_sort_key(name):
    name = name.upper()
    priority = ["S2", "S3", "S4", "S5", "S6", "MCA", "IMCA", "MBA"]
    for i, p in enumerate(priority):
        if name.startswith(p):
            return i, name
    return len(priority), name  # Others go last

def subject_sort_key(name):
    return name.upper()

def subject_wise_allocation(request):

    # Fetch all subject entries for current period
    subjects = SubjectEntry.objects.filter(period=dp).order_by("class_name", "subject_name")

    # Prefetch staff for each subject
    timetable_map = {}
    tt = TimetableEntry.objects.select_related("staff", "subject").filter(subject__period=dp)
    for t in tt:
        timetable_map.setdefault(t.subject_id, []).append(t.staff.name)

    # Prepare final data → grouped as SUBJECT : [{day,hours,lab,staff}]
    combined = {}

    for sub in subjects:
        key = f"{sub.subject_name} ({sub.class_name})"

        if key not in combined:
            combined[key] = []

        combined[key].append({
            "day": sub.get_day_display(),
            "hours": sub.allotted_hours,
            "lab": sub.LAB,
            "staff": ", ".join(timetable_map.get(sub.id, [])) or "—"
        })

    # Sort by class (S2/S4/MCA/IMCA) then subject
    sorted_data = OrderedDict(
        sorted(
            combined.items(),
            key=lambda x: (class_sort_key(x[0].split("(")[1].replace(")", "")), subject_sort_key(x[0]))
        )
    )

    return render(request, "subject_wise_allocation.html", {"data": sorted_data})




# ============================================================
#  DELETE SUBJECT ENTRY FORM
# ============================================================


@login_required(login_url="/")
def delete_subject_entry_view(request):
    if request.method == "POST":
        form = DeleteSubjectEntryForm(request.POST)
        if form.is_valid():
            form.delete_entry()
            return HttpResponse("sucess")
    else:
        form = DeleteSubjectEntryForm()
    return render(request, "delete_subject_entry.html", {"form": form})


# ============================================================
#  GOOGLE SIGN-IN
# ============================================================

from django.views.decorators.csrf import csrf_exempt

GOOGLE_CLIENT_ID = (
    "84125902506-9jqucnbkpegphqn5ku1g63au6l9hchiv.apps.googleusercontent.com"
)


@csrf_exempt
def google_auth_callback(request):
    print("haiiiiiiiiiiii")
    if request.method == "POST":
        data = json.loads(request.body)
        token = data.get("id_token")

        verify_url = f"https://oauth2.googleapis.com/tokeninfo?id_token={token}"
        response = requests.get(verify_url)

        if response.status_code == 200:
            user_info = response.json()
            if user_info["aud"] != GOOGLE_CLIENT_ID:
                return JsonResponse({"error": "Invalid client ID"}, status=400)

            request.session["user_email"] = user_info["email"]
            request.session["user_name"] = user_info.get("name", "")
            return JsonResponse({"redirect": "/allot/"})
        else:
            return JsonResponse({"error": "Invalid token"}, status=400)
    return JsonResponse({"error": "Only POST allowed"}, status=405)


def show_google_login_page(request):
    return render(request, "google_login.html")


@login_required
def home(request):
    return render(request, "home.html")


def logout_view(request):
    logout(request)
    return redirect("login")


@login_required
def dashboard_view(request):
    semesters = Semester.objects.all().order_by('name')
    active_sem = semesters.filter(is_active=True).first()
    
    if active_sem and 'selected_period' not in request.session:
        request.session['selected_period'] = active_sem.name
        
    current_view_sem = request.session.get('selected_period')
    
    return render(request, "dashboard.html", {
        "semesters": semesters,
        "active_sem": active_sem,
        "current_view_sem": current_view_sem
    })


#staff count for hover
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from .models import TimetableEntry
from django.conf import settings
# DP removed
@login_required
def staff_subject_count(request):
    """
    Returns JSON: { "count": <int> }
    Query params: staff_id, subject (subject_name)
    """
    staff_id = request.GET.get("staff_id")
    subject = request.GET.get("subject", "").strip()

    # basic validation
    if not staff_id or not subject:
        return JsonResponse({"count": 0})

    try:
        count = TimetableEntry.objects.filter(
            staff_id=staff_id,
            subject__subject_name__iexact=subject,
            subject__period=dp,
            user_id=request.user
        ).count()
    except Exception:
        count = 0

    return JsonResponse({"count": count})


#staff count get

def get_subject_load(request, staff_id, subject_id):
    count = TimetableEntry.objects.filter(
        staff_id=staff_id,
        subject_id=subject_id
    ).count()

    return JsonResponse({"count": count})

#staff day slot
from django.http import JsonResponse
from django.conf import settings
from .models import TimetableEntry
# DP removed
def get_staff_day_load(request, staff_id, day):
    """
    Returns staff's allocated hours on a given day.
    """
    entries = TimetableEntry.objects.filter(
        staff_id=staff_id,
        subject__day=day,
        subject__period=dp
    ).select_related("subject")

    result = []
    for e in entries:
        result.append({
            "subject": e.subject.subject_name,
            "hours": e.subject.allotted_hours
        })

    return JsonResponse({"entries": result})



# ============================================================
#  QUICK ALLOCATE / DELETE FOR ALLOTTED PAGE
# ============================================================


@login_required
def get_allotments_by_staff(request):
    staff_id = request.GET.get("staff_id")
    data = []

    if staff_id:
        entries = TimetableEntry.objects.filter(staff_id=staff_id).select_related(
            "subject"
        )
        data = [
            {
                "id": entry.id,
                "label": f"{entry.staff.name} - {entry.subject.subject_name} - {entry.subject.class_name} ({entry.subject.get_day_display()})",
            }
            for entry in entries
        ]

    return JsonResponse(data, safe=False)


@login_required
def quick_allocate(request, subject_id, staff_id):
    subject = SubjectEntry.objects.get(id=subject_id)
    staff = Staff.objects.get(id=staff_id)

    TimetableEntry.objects.create(staff=staff, subject=subject, user=request.user)
    return redirect("allotted")


@login_required
def quick_delete_staff(request, staff_id, subject_id):
    TimetableEntry.objects.filter(staff_id=staff_id, subject_id=subject_id).delete()
    return redirect("allotted")


# ============================================================
#  EDIT STAFF CONFIG (JSON)
# ============================================================


@login_required
def edit_staff_config(request):
    with open(CONFIG_FILE, "r") as f:
        data = f.read()

    if request.method == "POST":
        new_json = request.POST.get("content")

        try:
            json.loads(new_json)
            with open(CONFIG_FILE, "w") as f:
                f.write(new_json)
            message = "Configuration saved successfully!"
        except Exception:
            message = "Invalid JSON format!"

        return render(
            request, "staff_config.html", {"content": new_json, "message": message}
        )

    return render(request, "staff_config.html", {"content": data})


# ============================================================
#  APPLY AI ALLOCATION → SAVE DIRECTLY TO TimetableEntry
# ============================================================


@login_required
def apply_ai_allocation(request):
    """
    Run AI allocation fresh and push directly into TimetableEntry.
    Shows final summary: inserted, duplicates skipped, overlaps skipped.
    Uses SAME AI LOGIC as timetable2.
    """

    # 🔄 RELOAD RULES FRESH
    RULES = load_rules()

    global SENIORITY_ORDER, STAFF_PREF, MAX_WORKLOAD
    global SUBJECT_RULES, MAX_SUBJECT_ALLOTMENT, SAME_BATCH_PREF, COMMON_SUBJECTS

    SENIORITY_ORDER = RULES["SENIORITY_ORDER"]
    STAFF_PREF = RULES["PREFERENCES"]
    MAX_WORKLOAD = RULES["WORKLOAD"]
    SUBJECT_RULES = RULES["SUBJECT_RULES"]
    MAX_SUBJECT_ALLOTMENT = RULES["MAX_SUBJECT_ALLOTMENT"]
    SAME_BATCH_PREF = RULES["SAME_BATCH_PREF"]
    COMMON_SUBJECTS = set(RULES["COMMON_SUBJECTS"])

    # 1️⃣ CLEAR OLD RECORDS FOR THIS USER + PERIOD
    old_deleted, _ = TimetableEntry.objects.filter(
        user=request.user,
        subject__period=dp
    ).delete()

    inserted = 0
    duplicate_skipped = 0
    overlap_skipped = 0

    # 2️⃣ STAFF ORDER
    staff_all = list(Staff.objects.all())
    staff_list = sorted(
        staff_all,
        key=lambda s: SENIORITY_ORDER.index(s.name.strip())
        if s.name.strip() in SENIORITY_ORDER else 999
    )

    # 3️⃣ SUBJECTS SORTED
    subjects = list(SubjectEntry.objects.filter(period=dp))

    def subj_sort_key(sub):
        sname = sub.subject_name.upper()
        return (
            SUBJECT_PRIORITY.get(sname, 99),
            sub.class_name,
            sub.LAB,
            sub.day,
        )

    subjects.sort(key=subj_sort_key)

    # 4️⃣ INIT tracking
    staff_avail = {s.id: {'M': [], 'T': [], 'W': [], 'Th': [], 'F': []} for s in staff_list}
    staff_stats = {
        s.id: {"hours": 0, "subject_slots": {}, "batch_counts": {}}
        for s in staff_list
    }

    # 5️⃣ RUN AI ENGINE
    for sub in subjects:
        selected_staff = select_staff(sub, staff_list, staff_avail, staff_stats)
        if not selected_staff:
            continue

        hours = list(map(int, sub.allotted_hours.split(',')))
        pmin, pmax = adjusted_range(hours)
        subj_key = sub.subject_name.upper()

        for staff in selected_staff:
            sid = staff.id

            # CHECK DUPLICATE
            if TimetableEntry.objects.filter(
                user=request.user,
                staff=staff,
                subject=sub
            ).exists():
                duplicate_skipped += 1
                continue

            # CHECK TIME OVERLAP
            existing = TimetableEntry.objects.filter(
                user=request.user,
                staff=staff,
                subject__period=dp,
                subject__day=sub.day
            )

            adj_hours = set(adjusted_hour(h) for h in hours)
            clash = False
            for e in existing:
                ex_hours = set(adjusted_hour(int(h)) for h in e.subject.allotted_hours.split(','))
                if adj_hours.intersection(ex_hours):
                    clash = True
                    break

            if clash:
                overlap_skipped += 1
                continue

            # SAVE ENTRY
            TimetableEntry.objects.create(
                user=request.user,
                staff=staff,
                subject=sub
            )
            inserted += 1

            # UPDATE tracking for next allocations
            staff_avail[sid][sub.day].append((pmin, pmax))
            staff_stats[sid]["hours"] += (pmax - pmin + 1)
            staff_stats[sid]["subject_slots"][subj_key] = \
                staff_stats[sid]["subject_slots"].get(subj_key, 0) + 1

    # 6️⃣ RETURN RESULT PAGE
    return HttpResponse(f"""
        <h2>AI Allocation Completed</h2>
        <p><strong>Old Records Deleted:</strong> {old_deleted}</p>
        <p><strong>Inserted:</strong> {inserted}</p>
        <p><strong>Duplicates Skipped:</strong> {duplicate_skipped}</p>
        <p><strong>Overlaps Skipped:</strong> {overlap_skipped}</p>
        <br>
        <a href="/timetable2/" style="padding:10px; background:green; color:white; text-decoration:none;">Back to AI Preview</a>
        &nbsp;
        <a href="/timetable/" style="padding:10px; background:blue; color:white; text-decoration:none;">View My Timetable</a>
    """)


# ============================================================
#  AI TIMETABLE VIEW (ONLY PREVIEW – NO DB WRITE)
# ============================================================


@login_required
def timetable2(request):
    """
    AI-generated timetable preview.
    Uses dynamic JSON (staff_config.json) on EVERY REQUEST.
    Does NOT write to DB.
    """
    global RULES, SENIORITY_ORDER, STAFF_PREF, MAX_WORKLOAD
    global SUBJECT_RULES, MAX_SUBJECT_ALLOTMENT, SAME_BATCH_PREF, COMMON_SUBJECTS

    # 🔄 Reload rules fresh
    RULES = load_rules()
    SENIORITY_ORDER = RULES["SENIORITY_ORDER"]
    STAFF_PREF = RULES["PREFERENCES"]
    MAX_WORKLOAD = RULES["WORKLOAD"]
    SUBJECT_RULES = RULES["SUBJECT_RULES"]
    MAX_SUBJECT_ALLOTMENT = RULES["MAX_SUBJECT_ALLOTMENT"]
    SAME_BATCH_PREF = RULES["SAME_BATCH_PREF"]
    COMMON_SUBJECTS = set(RULES["COMMON_SUBJECTS"])

    # 1️⃣ ORDER STAFF BY SENIORITY
    staff_all = list(Staff.objects.all())
    staff_list = sorted(
        staff_all,
        key=lambda s: SENIORITY_ORDER.index(s.name.strip())
        if s.name.strip() in SENIORITY_ORDER
        else 999,
    )

    # 2️⃣ LOAD SUBJECTS FOR THIS PERIOD
    subjects = list(SubjectEntry.objects.filter(period=dp))

    def subj_sort_key(sub):
        sname = sub.subject_name.upper()
        return (
            SUBJECT_PRIORITY.get(sname, 99),
            sub.class_name,
            sub.LAB,
            sub.day,
        )

    subjects.sort(key=subj_sort_key)

    # 3️⃣ INIT
    staff_avail = {s.id: {"M": [], "T": [], "W": [], "Th": [], "F": []} for s in staff_list}
    staff_stats = {
        s.id: {"hours": 0, "subject_slots": {}, "batch_counts": {}} for s in staff_list
    }
    assigned_map = {s.id: [] for s in staff_list}

    # 4️⃣ ASSIGN SUBJECTS (AI LOGIC)
    for sub in subjects:
        selected = select_staff(sub, staff_list, staff_avail, staff_stats)
        if not selected:
            continue

        hours = [int(x) for x in sub.allotted_hours.split(",")]
        pmin, pmax = adjusted_range(hours)
        slot_count = pmax - pmin + 1
        subj_name = sub.subject_name.upper()
        key = f"{subj_name}__{sub.class_name}"

        for s in selected:
            sid = s.id
            staff_avail[sid][sub.day].append((pmin, pmax))
            staff_stats[sid]["hours"] += slot_count
            staff_stats[sid]["subject_slots"][subj_name] = (
                staff_stats[sid]["subject_slots"].get(subj_name, 0) + 1
            )
            staff_stats[sid]["batch_counts"][key] = (
                staff_stats[sid]["batch_counts"].get(key, 0) + 1
            )
            assigned_map[sid].append(sub)

    # 5️⃣ BUILD PREVIEW MATRIX
    staff_timetables = {}
    for s in staff_list:
        slots = [["" for _ in range(8)] for _ in range(5)]
        workload = staff_stats[s.id]["hours"]

        for sub in assigned_map[s.id]:
            hours = [int(x) for x in sub.allotted_hours.split(",")]
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
                        "colspan": end - start + 1,
                    }
                else:
                    slots[row][c] = None

        staff_timetables[s.name] = {
            "timetable_slots": slots,
            "total_hour": workload,
        }

    return render(
        request,
        "timetable_auto.html",
        {"staff_timetables": staff_timetables},
    )


from .models import SubjectFacultyMap
@login_required
def subject_faculty_mapping(request):
    subjects = SubjectEntry.objects.filter(period=dp).order_by("class_name", "subject_name", "id")

    # Load existing mappings
    mapping = {m.subject_id: m for m in SubjectFacultyMap.objects.filter(period=dp)}

    if request.method == "POST":
        for sub in subjects:
            field_name = f"faculty_{sub.id}"
            faculty_val = request.POST.get(field_name, "").strip()

            if faculty_val == "":
                continue  # skip empty

            # update OR create
            obj, created = SubjectFacultyMap.objects.update_or_create(
                subject=sub,
                period=dp,
                defaults={"faculty_names": faculty_val},
            )

        messages.success(request, "Faculty mapping updated successfully.")
        return redirect("subject_faculty_mapping")

    return render(request, "subject_faculty_mapping.html", {
        "subjects": subjects,
        "mapping": mapping
    })

#for calculate and download workload
import xlsxwriter
from collections import defaultdict
def export_final_workload(request):

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = 'attachment; filename=department_workload.xlsx'

    workbook = xlsxwriter.Workbook(response, {'in_memory': True})

    ws = workbook.add_worksheet("Course Workload")
    ws2 = workbook.add_worksheet("Staff Workload")

    header = workbook.add_format({'bold': True})
    right = workbook.add_format({'align': 'right'})

    # =====================================================================
    # SHEET 1 — SUBJECT WISE WITH BREAKDOWN
    # =====================================================================

    ws.write_row('A1', ["COURSE", "SUBJECT", "BREAKDOWN", "WORKLOAD"], header)

    row = 1
    total_workload = 0
    subject_groups = defaultdict(list)

    for s in SubjectEntry.objects.filter(period=dp):
        key = (s.class_name.strip(), s.subject_name.strip())
        subject_groups[key].append(s)

    for (cls, sub), slots in sorted(subject_groups.items()):

        staff_hours = defaultdict(int)

        for slot in slots:
            hrs = len(slot.allotted_hours.split(","))

            assigned = (
                TimetableEntry.objects
                .filter(subject=slot)
                .select_related("staff")
            )

            if assigned.exists():
                for e in assigned:
                    staff_hours[e.staff.name] += hrs
            else:
                staff_hours["UNASSIGNED"] += hrs

        vals = list(staff_hours.values())

        # Build breakdown string
        if len(set(vals)) == 1 and len(vals) > 1:
            breakdown = f"{vals[0]}*{len(vals)}"
        else:
            breakdown = "+".join(f"{h}*1" for h in sorted(vals, reverse=True))

        workload = sum(vals)
        total_workload += workload

        ws.write(row, 0, cls)
        ws.write(row, 1, sub)
        ws.write(row, 2, breakdown)
        ws.write_number(row, 3, workload, right)

        row += 1

    ws.write(row, 2, "TOTAL", header)
    ws.write_number(row, 3, total_workload, header)


    # =====================================================================
    # SHEET 2 — STAFF WISE (GROUPED)
    # =====================================================================

    ws2.write_row('A1', ["STAFF", "SUBJECT", "CLASS", "HOURS", "TOTAL"], header)

    row = 1
    dept_total = 0

    # Build grouped map
    staff_map = defaultdict(lambda: defaultdict(int))

    entries = (
        TimetableEntry.objects
        .filter(subject__period=dp)
        .select_related("staff", "subject")
        .order_by("staff__name")
    )

    for e in entries:
        hrs = len(e.subject.allotted_hours.split(","))
        key = (e.subject.subject_name.strip(), e.subject.class_name.strip())
        staff_map[e.staff.name][key] += hrs

    # Write sheet
    for staff in sorted(staff_map.keys()):

        running_total = 0
        block_start_row = row

        ws2.write(row, 0, staff, header)

        for (sub, cls), hrs in staff_map[staff].items():

            ws2.write(row, 1, sub)
            ws2.write(row, 2, cls)
            ws2.write(row, 3, hrs)

            running_total += hrs
            dept_total += hrs
            row += 1

        ws2.write(block_start_row, 4, running_total, header)
        row += 1  # blank row


    # FINAL TOTAL
    row += 1
    ws2.write(row, 3, "DEPARTMENT TOTAL", header)
    ws2.write(row, 4, dept_total, header)

    workbook.close()
    return response

def manage_batches(request):
    dp = get_current_period(request)
    if request.method == "POST":
        action = request.POST.get('action')
        if action == "add_batch":
            batch_name = request.POST.get('batch_name')
            period = request.POST.get('period', dp)
            if batch_name:
                Batch.objects.get_or_create(name=batch_name, period=period)
        elif action == "add_subject":
            batch_id = request.POST.get('batch_id')
            subject_name = request.POST.get('subject_name')
            if batch_id and subject_name:
                batch = Batch.objects.get(id=batch_id)
                BatchSubject.objects.create(batch=batch, subject_name=subject_name)
        elif action == "assign_batch_to_semester":
            batch_name = request.POST.get('batch_name')
            target_period = request.POST.get('period')
            if batch_name and target_period:
                target_batch, created = Batch.objects.get_or_create(name=batch_name, period=target_period)
                source_batch = Batch.objects.filter(name=batch_name).exclude(period=target_period).first()
                if source_batch:
                    for subject in source_batch.subjects.all():
                        BatchSubject.objects.get_or_create(batch=target_batch, subject_name=subject.subject_name)
        elif action == "add_semester":
            sem_name = request.POST.get('semester_name')
            if sem_name:
                Semester.objects.get_or_create(name=sem_name)
        elif action == "activate_semester":
            sem_id = request.POST.get('semester_id')
            if sem_id:
                try:
                    sem = Semester.objects.get(id=sem_id)
                    sem.is_active = True
                    sem.save()
                    request.session['selected_period'] = sem.name
                except Semester.DoesNotExist:
                    pass
        return redirect('manage_batches')
    
    semesters = Semester.objects.all().order_by('name')
    active_sem = semesters.filter(is_active=True).first()
    
    # Ensure active semester exists in session if it's set in db
    if active_sem and 'selected_period' not in request.session:
        request.session['selected_period'] = active_sem.name
        
    current_view_sem = request.session.get('selected_period') or dp
    batches = Batch.objects.filter(period=current_view_sem).prefetch_related('subjects').all()
    distinct_batch_names = Batch.objects.values_list('name', flat=True).distinct().order_by('name')
    
    return render(request, 'manage_batches.html', {
        'batches': batches,
        'semesters': semesters,
        'active_sem': active_sem,
        'current_view_sem': current_view_sem,
        'distinct_batch_names': distinct_batch_names
    })

def switch_semester(request):
    if request.method == "POST":
        sem_name = request.POST.get('semester_name')
        if sem_name:
            request.session['selected_period'] = sem_name
    return redirect(request.META.get('HTTP_REFERER', 'timetable'))

def subject_entry_view(request):
    dp = get_current_period(request)
    if request.method == "POST":
        batch_id = request.POST.get('batch_id')
        subject_name = request.POST.get('subject_name')
        if batch_id and subject_name:
            batch = Batch.objects.get(id=batch_id)
            
            day_1 = request.POST.get('day_1')
            hours_1 = request.POST.get('hours_1')
            if day_1 and hours_1:
                SubjectEntry.objects.create(
                    subject_name=subject_name, 
                    class_name=batch.name, 
                    day=day_1, 
                    allotted_hours=hours_1,
                    period=dp
                )
            
            day_2 = request.POST.get('day_2')
            hours_2 = request.POST.get('hours_2')
            if day_2 and hours_2:
                SubjectEntry.objects.create(
                    subject_name=subject_name, 
                    class_name=batch.name, 
                    day=day_2, 
                    allotted_hours=hours_2,
                    period=dp
                )
            
            return redirect('subject_entry')
    
    batches = Batch.objects.filter(period=dp)
    return render(request, 'subject_entry.html', {'batches': batches})

def get_batch_subjects(request, batch_id):
    subjects = BatchSubject.objects.filter(batch_id=batch_id).values('id', 'subject_name')
    return JsonResponse({'subjects': list(subjects)})

def get_batch_allotments(request, batch_id):
    dp = get_current_period(request)
    try:
        batch = Batch.objects.get(id=batch_id)
        allotments = SubjectEntry.objects.filter(class_name=batch.name, period=dp).values('id', 'subject_name', 'day', 'allotted_hours')
        # map day codes to display names
        day_map = dict(SubjectEntry.DAY_CHOICES)
        allotments_list = list(allotments)
        for a in allotments_list:
            a['day_display'] = day_map.get(a['day'], a['day'])
        return JsonResponse({'allotments': allotments_list})
    except Batch.DoesNotExist:
        return JsonResponse({'allotments': []})

@login_required
def palette_allocate(request, subject_id, staff_id):
    subject = get_object_or_404(SubjectEntry, id=subject_id)
    staff = get_object_or_404(Staff, id=staff_id)
    
    # Check if this exact SubjectEntry is already allotted to this staff
    exists = TimetableEntry.objects.filter(staff=staff, subject=subject).exists()
    if not exists:
        TimetableEntry.objects.create(staff=staff, subject=subject, user=request.user)
    
    return redirect("timetable")
