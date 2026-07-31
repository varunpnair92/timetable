from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.db import IntegrityError, transaction
from django.core.exceptions import ValidationError


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


def get_day_labels(day, layout_type):
    if layout_type == 'new':
        return ["1", "2", "3", "4", "5", "6"]
    else:
        return ["H1", "H2", "H3", "H4", "LB", "H5", "H6", "H7"]


@login_required(login_url="/")
def timetable(request):
    dp = get_current_period(request)
    try:
        sem = Semester.objects.get(name=dp)
        layout_type = sem.layout_type
    except Semester.DoesNotExist:
        layout_type = 'classic'

    staff_members = Staff.objects.all()
    staff_timetables = {}

    for staff in staff_members:

        # 5 days × dynamic hours grid
        timetable_slots = []
        days_keys = ["M", "T", "W", "Th", "F"]
        for dkey in days_keys:
            day_labels = get_day_labels(dkey, layout_type)
            row_slots = []
            for lbl in day_labels:
                row_slots.append({
                    "hour_label": lbl,
                    "subject": None
                })
            timetable_slots.append(row_slots)

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

            adjusted_hours = []
            if layout_type == 'new':
                if day == 'F':
                    # Friday: 1->1, 2->2, 3->3, 4->4, 5->5, 8(LB)->6, 6->7
                    for hour in hours:
                        if hour == '8':
                            adjusted_hours.append('6')
                        elif hour == '6':
                            adjusted_hours.append('7')
                        else:
                            adjusted_hours.append(hour)
                else:
                    # Mon-Thu: 1->1, 2->2, 3->3, 4->4, 5->5, 6->6
                    for hour in hours:
                        adjusted_hours.append(hour)
            else:
                # Adjust hours: 8→5, 5→6, 6→7, 7→8
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

            max_index = 6 if layout_type == 'new' else 7
            if start_index < 0: 
                start_index = 0
            if end_index > max_index:
                end_index = max_index

            # Fill timetable grid
            for col_index in range(start_index, end_index + 1):
                if col_index < len(timetable_slots[row_index]):
                    if col_index == start_index:
                        # ⭐ MAIN SLOT – We store entry_id here
                        timetable_slots[row_index][col_index] = {
                            "hour_label": timetable_slots[row_index][col_index]["hour_label"],
                            "lab": subject_entry.LAB,
                            "class_name": subject_entry.class_name,
                            "subject": subject_entry.subject_name,
                            "entry_id": entry.id,     # ⭐ IMPORTANT
                            "colspan": min(end_index, len(timetable_slots[row_index]) - 1) - start_index + 1,
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
    
    # Retrieve all TimetableEntries for these subjects & user to avoid N+1 queries
    from collections import defaultdict
    allotment_map = defaultdict(list)
    entries = TimetableEntry.objects.filter(
        subject__period=dp,
        user=request.user
    ).select_related('staff')
    for entry in entries:
        allotment_map[entry.subject_id].append({
            'entry_id': entry.id,
            'staff_id': entry.staff.id,
            'name': entry.staff.name
        })

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
            'lab': sub.LAB,
            'allotted_staff': allotment_map[sub.id],
        })

    semesters = Semester.objects.all().order_by('name')
    return render(request, "timetable.html", {
        "staff_timetables": staff_timetables, 
        "has_undo": has_undo,
        "palette_subjects": palette_subjects,
        "semesters": semesters,
        "current_view_sem": dp,
        "layout_type": layout_type,
    })

# ============================================================
#  CLASS WISE ALLOTTED VIEW
# ============================================================


@login_required(login_url="/")
def allotted(request):
    dp = get_current_period(request)
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
    dp = get_current_period(request)
    """
    Returns list of staff free for the subject's slot, plus their AI Rank, Reason,
    and the number of slots that staff already has for the same subject.
    JSON: [{id, name, count, rank, reason}, ...]
    """
    subject = get_object_or_404(SubjectEntry, id=subject_id, period=dp)

    RULES = load_rules()
    STAFF_PREF = RULES.get("PREFERENCES", {})
    MAX_WORKLOAD = RULES.get("WORKLOAD", {})
    MAX_SUBJECT_ALLOTMENT = RULES.get("MAX_SUBJECT_ALLOTMENT", {})
    COMMON_SUBJECTS = set(RULES.get("COMMON_SUBJECTS", []))
    DEFAULT_MAX_WORKLOAD = 22

    # target info
    target_day = subject.day
    try:
        target_hours = set(int(x) for x in subject.allotted_hours.split(',') if x.strip() != '')
    except Exception:
        target_hours = set()
    
    target_subj = subject.subject_name.upper()

    staff_qs = Staff.objects.all().order_by('name')
    
    # Pre-fetch all entries for the given period to avoid N+1 queries
    all_period_entries = TimetableEntry.objects.filter(subject__period=dp).select_related('staff', 'subject')
    entries_by_staff = {}
    for e in all_period_entries:
        if e.staff_id not in entries_by_staff:
            entries_by_staff[e.staff_id] = []
        entries_by_staff[e.staff_id].append(e)

    free_staff = []

    for st in staff_qs:
        # Check timetable for this staff
        all_entries = entries_by_staff.get(st.id, [])
        
        # 1) Time conflict
        busy = False
        total_hours_for_staff = 0
        subject_slot_count = 0
        same_batch = False

        for e in all_entries:
            try:
                e_hours = [int(x) for x in e.subject.allotted_hours.split(',') if x.strip() != '']
            except Exception:
                e_hours = []
                
            total_hours_for_staff += len(e_hours)
            
            if e.subject.subject_name.upper() == target_subj:
                subject_slot_count += len(e_hours)
                if e.subject.class_name == subject.class_name:
                    same_batch = True
                    
            if e.subject.day == target_day:
                if target_hours.intersection(set(e_hours)):
                    busy = True

        if busy:
            continue

        # 2) Workload check
        hours_needed = len(target_hours)
        max_hours = MAX_WORKLOAD.get(st.name, DEFAULT_MAX_WORKLOAD)
        if total_hours_for_staff + hours_needed > max_hours:
            continue # Over workload

        # 3) Subject entry limit
        limit = MAX_SUBJECT_ALLOTMENT.get(target_subj, 999)
        # Count rows for this subject
        rows_for_subject = sum(1 for e in all_entries if e.subject.subject_name.upper() == target_subj)
        if rows_for_subject + 1 > limit:
            continue

        # Determine AI Rank
        rank = 3
        reason = "Available"
        prefs = STAFF_PREF.get(st.name, ["", ""])
        
        if same_batch:
            rank = 1
            reason = "Same Batch"
        elif prefs and prefs[0].upper() == target_subj:
            rank = 1
            reason = "1st Preference"
        elif len(prefs) > 1 and prefs[1].upper() == target_subj:
            rank = 2
            reason = "2nd Preference"
        elif target_subj in COMMON_SUBJECTS:
            rank = 2
            reason = "Common Subject"

        free_staff.append({
            "id": st.id,
            "name": st.name,
            "count": subject_slot_count,
            "rank": rank,
            "reason": reason
        })

    # Sort by rank ascending, then by current load ascending
    free_staff.sort(key=lambda x: (x["rank"], x["count"]))

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
    heading_style = request.GET.get("heading", "new")
    orientation = request.GET.get("orientation", "landscape")
    import xlsxwriter
    from .models import SubjectFacultyMap

    logo_path = os.path.join(settings.BASE_DIR, "static", "fisat_logo.png")

    labs = (
        SubjectEntry.objects.filter(period=dp)
        .values_list("LAB", flat=True)
        .distinct()
    )

    try:
        sem = Semester.objects.get(name=dp)
        layout_type = sem.layout_type
    except Semester.DoesNotExist:
        layout_type = 'classic'

    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output)

    # ======= FORMATS =======
    institute_fmt = workbook.add_format({
        "text_wrap": True,
        "bold": True, "font_size": 20,
        "align": "center", "valign": "vcenter"
    })
    
    # PDF Style Formats
    fisat_fmt = workbook.add_format({
        "bold": True, "font_size": 24, "font_name": "Times New Roman",
        "font_color": "#2e3192", "align": "center", "valign": "vcenter"
    })
    sub_fmt = workbook.add_format({
        "bold": True, "font_size": 12, "font_name": "Times New Roman",
        "font_color": "#2e3192", "align": "center", "valign": "vcenter"
    })
    auto_fmt = workbook.add_format({
        "bold": True, "font_size": 11, "font_name": "Arial",
        "font_color": "#f26522", "align": "center", "valign": "vcenter"
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
    black_fmt = workbook.add_format({
        "bg_color": "black",
        "font_color": "white",
        "align": "center",
        "valign": "vcenter",
        "bold": True,
        "border": 1
    })

    staff_abbr = {
        "AMBILY N MENON": "ANM", "SREELALITHAMBIKA P K": "SL",
        "SANDYA O C": "SOC", "NEEBA CHERIYACHAN": "NC",
        "NOMA MATHEW": "NM", "AMBILY SEKAR C": "AS",
        "VARUN P NAIR": "VPN", "ARAVIND BALAN": "AB",
        "SALINI T R": "STR", "SMIJA M B": "SM", "JOYCY": "JY",
    }

    days = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    day_map = {"M":"Mon","T":"Tue","W":"Wed","Th":"Thu","F":"Fri"}

    if layout_type == 'new':
        hours = ["H1", "H2", "H3", "H4", "H5", "H6"]
        last_col = "G"
    else:
        hours = ["H1", "H2", "H3", "H4", "LB", "H5", "H6", "H7"]
        last_col = "I"

    for lab in labs:
        ws = workbook.add_worksheet(lab)
        ws.set_paper(9)                # A4
        if orientation == "portrait":
            ws.set_portrait()
        else:
            ws.set_landscape()
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
        if heading_style == "pdf":
            ws.merge_range(f"B1:{last_col}1", "FISAT®", fisat_fmt)
            ws.merge_range(f"B2:{last_col}2", "FEDERAL INSTITUTE OF SCIENCE AND TECHNOLOGY", sub_fmt)
            ws.merge_range(f"B3:{last_col}3", "AUTONOMOUS", auto_fmt)
            ws.merge_range(f"A4:{last_col}4", "LAB TIMETABLE FOR B.TECH (DEC 2025 – MAY 2025)", title_fmt)
            # The LAB NAME HEADER is rendered after this, but we need to push it down
            # Actually, the lab header is merged at A4 normally. We need to shift it to A5 if heading_style is pdf.
        elif heading_style == "old":
            ws.merge_range(f"B1:{last_col}1", "Federal Institute of Science And Technology(FISAT)", institute_fmt)
            ws.merge_range(f"B2:{last_col}2", "Hormis Nagar,Angamaly", address_fmt)
            ws.merge_range(f"B3:{last_col}3", "Department Of Computer Science And Engineering", address_fmt)
            ws.merge_range(f"A4:{last_col}4", "LAB TIMETABLE FOR B.TECH (DEC 2025 – MAY 2025)", title_fmt)
        else:
            ws.merge_range(f"A1:{last_col}1", "FEDERAL INSTITUTE OF SCIENCE AND TECHNOLOGY (FISAT)\n(Hormis Nagar, Mookkannoor, Angamaly, Kerala – 683577)\nLAB TIMETABLE FOR B.TECH (DEC 2025 – MAY 2025)", institute_fmt)

        # ⭐⭐⭐ LAB NAME HEADER MERGED ABOVE HOURS ⭐⭐⭐
        if heading_style in ["pdf", "old"]:
            ws.merge_range(f"A5:{last_col}5", f"CCF : {lab}", lab_header_fmt)
            table_row_start = 5
        else:
            ws.merge_range(f"A4:{last_col}4", f"CCF : {lab}", lab_header_fmt)
            table_row_start = 4

        # ========== TABLE HEADER ==========
        ws.write(table_row_start, 0, "Day", header_fmt)
        for c, h in enumerate(hours):
            ws.write(table_row_start, c + 1, h, header_fmt)

        row = table_row_start + 1
        subjects = SubjectEntry.objects.filter(LAB=lab, period=dp).order_by("day")

        for day in days:
            ws.write(row, 0, day, data_fmt)
            key = [k for k, v in day_map.items() if v == day][0]
            subs = subjects.filter(day=key)
            
            merged_cols = set()

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

                # Dynamic hour columns index mapping
                col_indices = []
                for h_str in sub.allotted_hours.split(","):
                    h = int(h_str)
                    if layout_type == 'new':
                        if key == 'F':
                            # Friday: 1->0, 2->1, 3->2, 4->3, 5->4, 8(LB)->5, 6->6
                            if h == 8:
                                col_indices.append(5)
                            elif h == 6:
                                col_indices.append(6)
                            else:
                                col_indices.append(h - 1)
                        else:
                            # Mon-Thu: 1->0, 2->1, 3->2, 4->3, 5->4, 6->5
                            col_indices.append(h - 1)
                    else:
                        # Classic: 1->0, 2->1, 3->2, 4->3, 8(LB)->4, 5->5, 6->6, 7->7
                        if h == 8:
                            col_indices.append(4)
                        elif h == 5:
                            col_indices.append(5)
                        elif h == 6:
                            col_indices.append(6)
                        elif h == 7:
                            col_indices.append(7)
                        else:
                            col_indices.append(h - 1)

                col_indices = sorted(set(col_indices))
                if not col_indices:
                    continue
                s = col_indices[0]
                e = col_indices[-1]

                overlap = any(s <= c_idx <= e for c_idx in merged_cols)

                if s == e:
                    ws.write(row, s + 1, text, merge_fmt)
                    for c_idx in range(s, e + 1):
                        merged_cols.add(c_idx)
                elif not overlap:
                    ws.merge_range(row, s + 1, row, e + 1, text, merge_fmt)
                    for c_idx in range(s, e + 1):
                        merged_cols.add(c_idx)
                else:
                    for c_idx in range(s, e + 1):
                        ws.write(row, c_idx + 1, text, merge_fmt)
                        merged_cols.add(c_idx)

            for col_idx in range(len(hours)):
                if col_idx not in merged_cols:
                    if layout_type == 'new' and key == 'F' and col_idx == 5:
                        ws.write(row, col_idx + 1, "LB", black_fmt)
                    else:
                        ws.write(row, col_idx + 1, "", empty_fmt)

            row += 1

        ws.set_column("A:A", 7)
        ws.set_column(f"B:{last_col}", 11)
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
    dp = get_current_period(request)
    heading_style = request.GET.get("heading", "new")
    orientation = request.GET.get("orientation", "landscape")
    import xlsxwriter
    from .models import SubjectFacultyMap
    from xlsxwriter.utility import xl_col_to_name

    logo_path = os.path.join(settings.BASE_DIR, "static", "fisat_logo.png")

    try:
        sem = Semester.objects.get(name=dp)
        layout_type = sem.layout_type
    except Semester.DoesNotExist:
        layout_type = 'classic'

    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output)
    ws = workbook.add_worksheet("Combined Labs")
    ws.set_paper(9)
    if orientation == "portrait":
        ws.set_portrait()
    else:
        ws.set_landscape()
    ws.center_horizontally()
    ws.center_vertically()
    ws.fit_to_pages(1, 1)
    ws.set_margins(left=0.3, right=0.3, top=0.5, bottom=0.5)

    # ===== FORMATS =====
    institute_fmt = workbook.add_format({
        "text_wrap": True,"bold": True, "font_size": 20,
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
    black_fmt = workbook.add_format({
        "bg_color": "black",
        "font_color": "white",
        "align": "center",
        "valign": "vcenter",
        "bold": True,
        "border": 1
    })

    staff_abbr = {
        "AMBILY N MENON": "ANM", "SREELALITHAMBIKA P K": "SL",
        "SANDYA O C": "SOC", "NEEBA CHERIYACHAN": "NC",
        "NOMA MATHEW": "NM", "AMBILY SEKAR C": "AS",
        "VARUN P NAIR": "VPN", "ARAVIND BALAN": "AB",
        "SALINI T R": "STR", "SMIJA M B": "SM", "JOYCY": "JY",
    }

    days = ["Mon","Tue","Wed","Thu","Fri"]
    day_map = {"M":"Mon","T":"Tue","W":"Wed","Th":"Thu","F":"Fri"}

    if layout_type == 'new':
        hours = ["H1", "H2", "H3", "H4", "H5", "H6"]
    else:
        hours = ["H1", "H2", "H3", "H4", "LB", "H5", "H6", "H7"]

    lab_groups = [
        ["L1","L2","L3"],
        ["L5","L7","L8"],
        ["L4","L6"],
        ["L9","PG LAB"]
    ]

    max_labs = max(len(g) for g in lab_groups)
    total_cols = (len(hours) + 3) * (max_labs - 1) + len(hours) + 1
    last_col_idx = total_cols - 1
    last_col_letter = xl_col_to_name(last_col_idx)
    title_end_letter = xl_col_to_name(max(1, last_col_idx - 5))

    # ===== LOGO =====
    try:
        ws.insert_image("A1", logo_path, {"x_scale": 0.3, "y_scale": 0.3})
    except:
        pass

    # ===== SINGLE ROW HEADER =====
    if heading_style == "pdf":
        ws.merge_range(f"B1:{last_col_letter}1", "FISAT®", fisat_fmt)
        ws.merge_range(f"B2:{title_end_letter}2", "FEDERAL INSTITUTE OF SCIENCE AND TECHNOLOGY", sub_fmt)
        ws.merge_range(f"B3:{title_end_letter}3", "AUTONOMOUS", auto_fmt)
        ws.merge_range(f"A4:{title_end_letter}4", "COMBINED LAB TIMETABLE FOR B.TECH (DEC 2025 – MAY 2025)", title_fmt)
    elif heading_style == "old":
        ws.merge_range(f"B1:{last_col_letter}1", "Federal Institute of Science And Technology(FISAT)", institute_fmt)
        ws.merge_range(f"B2:{title_end_letter}2", "Hormis Nagar,Angamaly", address_fmt)
        ws.merge_range(f"B3:{title_end_letter}3", "Department Of Computer Science And Engineering", address_fmt)
        ws.merge_range(f"A4:{title_end_letter}4", "COMBINED LAB TIMETABLE FOR B.TECH (DEC 2025 – MAY 2025)", title_fmt)
    else:
        ws.merge_range(f"A1:{last_col_letter}1", "FEDERAL INSTITUTE OF SCIENCE AND TECHNOLOGY (FISAT)\n(Hormis Nagar, Mookkannoor, Angamaly, Kerala – 683577)\nCOMBINED LAB TIMETABLE FOR B.TECH (DEC 2025 – MAY 2025)", institute_fmt)

    if heading_style in ["pdf", "old"]:
        start_row = 5
    else:
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
                    entries = TimetableEntry.objects.filter(subject=sub, user=request.user)
                    staff_names = ",".join(staff_abbr.get(e.staff.name,e.staff.name)
                                           for e in entries) or "—"

                    # faculty
                    try:
                        fac = SubjectFacultyMap.objects.get(subject=sub)
                        faculty = fac.faculty_names or "—"
                    except:
                        faculty = "—"

                    text = f"{sub.subject_name} ({sub.class_name})\n({faculty}) ({staff_names})"

                    col_indices = []
                    for h_str in sub.allotted_hours.split(","):
                        h = int(h_str)
                        if layout_type == 'new':
                            if key == 'F':
                                # Friday: 1->0, 2->1, 3->2, 4->3, 5->4, 8(LB)->5, 6->6
                                if h == 8:
                                    col_indices.append(5)
                                elif h == 6:
                                    col_indices.append(6)
                                else:
                                    col_indices.append(h - 1)
                            else:
                                # Mon-Thu: 1->0, 2->1, 3->2, 4->3, 5->4, 6->5
                                col_indices.append(h - 1)
                        else:
                            # Classic: 1->0, 2->1, 3->2, 4->3, 8(LB)->4, 5->5, 6->6, 7->7
                            if h == 8:
                                col_indices.append(4)
                            elif h == 5:
                                col_indices.append(5)
                            elif h == 6:
                                col_indices.append(6)
                            elif h == 7:
                                col_indices.append(7)
                            else:
                                col_indices.append(h - 1)

                    col_indices = sorted(set(col_indices))
                    if not col_indices:
                        continue
                    s = col_indices[0]
                    e = col_indices[-1]

                    if row not in merged:
                        merged[row] = []

                    merge_key = (col+1+s, col+1+e)

                    overlap = any(ms<=merge_key[0]<=me or ms<=merge_key[1]<=me
                                  for (ms,me) in merged[row])

                    if s == e:
                        ws.write(row, col+1+s, text, merge_fmt)
                        merged[row].append((col+1+s, col+1+e))
                    elif not overlap:
                        ws.merge_range(row, col+1+s, row, col+1+e, text, merge_fmt)
                        merged[row].append((col+1+s, col+1+e))
                    else:
                        for c in range(col+1+s, col+1+e+1):
                            ws.write(row, c, text, merge_fmt)
                        merged[row].append((col+1+s, col+1+e))

                # free slots
                for col_idx in range(len(hours)):
                    c = col + 1 + col_idx
                    if not any(ms<=c<=me for (ms,me) in merged.get(row,[])):
                        if layout_type == 'new' and key == 'F' and col_idx == 5:
                            ws.write(row, c, "LB", black_fmt)
                        else:
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
    dp = get_current_period(request)
    # Create the HTTP response for CSV
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="subject_entries_{dp}.csv"'

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
    dp = get_current_period(request)
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
    dp = get_current_period(request)

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
    from .models import Document, DocumentCategory
    semesters = Semester.objects.all().order_by('name')
    active_sem = semesters.filter(is_active=True).first()
    
    if active_sem and 'selected_period' not in request.session:
        request.session['selected_period'] = active_sem.name
        
    current_view_sem = request.session.get('selected_period')
    
    documents = Document.objects.filter(user=request.user).order_by('-created_at')
    categories = DocumentCategory.objects.all().order_by('name')
    
    return render(request, "dashboard.html", {
        "semesters": semesters,
        "active_sem": active_sem,
        "current_view_sem": current_view_sem,
        "documents": documents,
        "categories": categories
    })




#staff count for hover
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from .models import TimetableEntry
from django.conf import settings
# DP removed
@login_required
def staff_subject_count(request):
    dp = get_current_period(request)
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
    dp = get_current_period(request)
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
    dp = get_current_period(request)
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
    dp = get_current_period(request)
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
    try:
        sem = Semester.objects.get(name=dp)
        layout_type = sem.layout_type
    except Semester.DoesNotExist:
        layout_type = 'classic'

    staff_timetables = {}
    for s in staff_list:
        # 5 days × dynamic hours grid
        slots = []
        days_keys = ["M", "T", "W", "Th", "F"]
        for dkey in days_keys:
            day_labels = get_day_labels(dkey, layout_type)
            row_slots = []
            for lbl in day_labels:
                row_slots.append({
                    "hour_label": lbl,
                    "subject": None
                })
            slots.append(row_slots)

        workload = staff_stats[s.id]["hours"]

        for sub in assigned_map[s.id]:
            hours = [int(x) for x in sub.allotted_hours.split(",")]
            row = {"M": 0, "T": 1, "W": 2, "Th": 3, "F": 4}[sub.day]

            adjusted_hours = []
            if layout_type == 'new':
                if sub.day == 'F':
                    # Friday: 1->1, 2->2, 3->3, 4->4, 5->5, 8(LB)->6, 6->7
                    for hour in hours:
                        if hour == 8:
                            adjusted_hours.append(6)
                        elif hour == 6:
                            adjusted_hours.append(7)
                        else:
                            adjusted_hours.append(hour)
                else:
                    # Mon-Thu: 1->1, 2->2, 3->3, 4->4, 5->5, 6->6
                    for hour in hours:
                        adjusted_hours.append(hour)
            else:
                # Adjust hours: 8→5, 5→6, 6→7, 7→8
                for hour in hours:
                    if hour == 8:
                        adjusted_hours.append(5)
                    elif hour == 5:
                        adjusted_hours.append(6)
                    elif hour == 6:
                        adjusted_hours.append(7)
                    elif hour == 7:
                        adjusted_hours.append(8)
                    else:
                        adjusted_hours.append(hour)

            adj = sorted(set(adjusted_hours))
            start = adj[0] - 1
            end = adj[-1] - 1

            max_index = 6 if layout_type == 'new' else 7
            if start < 0:
                start = 0
            if end > max_index:
                end = max_index

            for c in range(start, end + 1):
                if c < len(slots[row]):
                    if c == start:
                        slots[row][c] = {
                            "hour_label": slots[row][c]["hour_label"],
                            "subject": sub.subject_name,
                            "class_name": sub.class_name,
                            "lab": sub.LAB,
                            "colspan": min(end, len(slots[row]) - 1) - start + 1,
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
        {
            "staff_timetables": staff_timetables,
            "layout_type": layout_type,
        },
    )


from .models import SubjectFacultyMap
@login_required
def subject_faculty_mapping(request):
    dp = get_current_period(request)
    subjects = SubjectEntry.objects.filter(period=dp).order_by("class_name", "subject_name", "id")

    if request.method == "POST":
        for sub in subjects:
            field_name = f"faculty_{sub.id}"
            faculty_val = request.POST.get(field_name, "").strip()

            if faculty_val == "":
                SubjectFacultyMap.objects.filter(subject=sub, period=dp).delete()
                continue

            # update OR create
            obj, created = SubjectFacultyMap.objects.update_or_create(
                subject=sub,
                period=dp,
                defaults={"faculty_names": faculty_val},
            )

        messages.success(request, "Faculty mapping updated successfully.")
        return redirect("subject_faculty_mapping")

    # Load existing mappings
    mapping = {m.subject_id: m for m in SubjectFacultyMap.objects.filter(period=dp)}
    for sub in subjects:
        sub.faculty_names = mapping.get(sub.id).faculty_names if sub.id in mapping else ""

    return render(request, "subject_faculty_mapping.html", {
        "subjects": subjects
    })

#for calculate and download workload
import xlsxwriter
from collections import defaultdict
def export_final_workload(request):
    dp = get_current_period(request)

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
            if batch_name:
                Batch.objects.get_or_create(name=batch_name, period=dp)
        elif action == "add_subject":
            batch_ids = request.POST.getlist('batch_id')
            subject_names_raw = request.POST.get('subject_name', '')
            hours = int(request.POST.get('hours', 1))
            if batch_ids and subject_names_raw:
                subject_names = [s.strip() for s in subject_names_raw.split(',') if s.strip()]
                for batch_id in batch_ids:
                    try:
                        batch = Batch.objects.get(id=batch_id)
                        for subject_name in subject_names:
                            BatchSubject.objects.create(batch=batch, subject_name=subject_name, hours=hours)
                    except Batch.DoesNotExist:
                        pass
        elif action == "assign_batch_to_semester":
            batch_names = request.POST.getlist('batch_names')
            target_period = request.POST.get('period')
            if batch_names and target_period:
                for name in batch_names:
                    target_batch, created = Batch.objects.get_or_create(name=name, period=target_period)
                    source_batch = Batch.objects.filter(name=name).exclude(period=target_period).first()
                    if source_batch:
                        for subject in source_batch.subjects.all():
                            BatchSubject.objects.get_or_create(batch=target_batch, subject_name=subject.subject_name)
        elif action == "add_semester":
            sem_name = request.POST.get('semester_name')
            layout_type = request.POST.get('layout_type', 'classic')
            if sem_name:
                Semester.objects.get_or_create(name=sem_name, defaults={'layout_type': layout_type})
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
        elif action == "edit_semester_layout":
            sem_id = request.POST.get('semester_id')
            layout_type = request.POST.get('layout_type')
            if sem_id and layout_type:
                try:
                    sem = Semester.objects.get(id=sem_id)
                    sem.layout_type = layout_type
                    sem.save()
                except Semester.DoesNotExist:
                    pass
        elif action == "delete_semester":
            sem_id = request.POST.get('semester_id')
            if sem_id:
                try:
                    Semester.objects.get(id=sem_id).delete()
                except Semester.DoesNotExist:
                    pass
        elif action == "delete_batch":
            batch_id = request.POST.get('batch_id')
            if batch_id:
                Batch.objects.filter(id=batch_id).delete()
        elif action == "delete_subject":
            subject_id = request.POST.get('subject_id')
            if subject_id:
                BatchSubject.objects.filter(id=subject_id).delete()
        return redirect('manage_batches')
    
    semesters = Semester.objects.all().order_by('name')
    active_sem = semesters.filter(is_active=True).first()
    
    # Ensure active semester exists in session if it's set in db
    if active_sem and 'selected_period' not in request.session:
        request.session['selected_period'] = active_sem.name
        
    current_view_sem = request.session.get('selected_period') or dp
    batches = Batch.objects.filter(period=current_view_sem).prefetch_related('subjects').all()
    
    # Group all existing batches and their subjects across all semesters
    all_batches = Batch.objects.prefetch_related('subjects').all()
    batch_map = {}
    for b in all_batches:
        if b.name not in batch_map:
            batch_map[b.name] = set()
        for s in b.subjects.all():
            batch_map[b.name].add(s.subject_name)
            
    distinct_batches_with_subjects = [
        {'name': name, 'subjects': sorted(list(subjects))}
        for name, subjects in sorted(batch_map.items())
    ]
    
    return render(request, 'manage_batches.html', {
        'batches': batches,
        'semesters': semesters,
        'active_sem': active_sem,
        'current_view_sem': current_view_sem,
        'distinct_batches_with_subjects': distinct_batches_with_subjects
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
        lab = request.POST.get('lab')
        
        if batch_id and subject_name:
            batch = Batch.objects.get(id=batch_id)
            errors = []
            
            def save_entry(day, hours):
                try:
                    entry = SubjectEntry(
                        subject_name=subject_name,
                        class_name=batch.name,
                        day=day,
                        allotted_hours=hours,
                        LAB=lab,
                        period=dp
                    )
                    entry.full_clean()
                    entry.save()
                except ValidationError as e:
                    errors.append(f"Allotment error: {', '.join(e.messages)}")
                except IntegrityError as e:
                    errors.append(f"Database error (possible duplicate key or sequence out of sync): {str(e)}")
            
            day_1 = request.POST.get('day_1')
            hours_1 = request.POST.get('hours_1')
            if day_1 and hours_1:
                save_entry(day_1, hours_1)
            
            day_2 = request.POST.get('day_2')
            hours_2 = request.POST.get('hours_2')
            if day_2 and hours_2 and not errors:
                save_entry(day_2, hours_2)
            
            if errors:
                for error in errors:
                    messages.error(request, error)
            else:
                messages.success(request, "Success! Subject entries have been allotted.")
            
            return redirect('subject_entry')
    
    semesters = Semester.objects.all().order_by('name')
    active_sem = semesters.filter(is_active=True).first()
    current_view_sem = get_current_period(request)

    batches = Batch.objects.filter(period=current_view_sem)
    lab_choices = SubjectEntry.LAB_CHOICES
    return render(request, 'subject_entry.html', {
        'batches': batches,
        'lab_choices': lab_choices,
        'semesters': semesters,
        'active_sem': active_sem,
        'current_view_sem': current_view_sem
    })

def get_batch_subjects(request, batch_id):
    subjects = BatchSubject.objects.filter(batch_id=batch_id).values('id', 'subject_name')
    return JsonResponse({'subjects': list(subjects)})

def get_batch_allotments(request, batch_id):
    dp = get_current_period(request)
    try:
        batch = Batch.objects.get(id=batch_id)
        allotments = SubjectEntry.objects.filter(class_name=batch.name, period=dp).values('id', 'subject_name', 'day', 'allotted_hours', 'LAB')
        # map day codes to display names
        day_map = dict(SubjectEntry.DAY_CHOICES)
        lab_map = dict(SubjectEntry.LAB_CHOICES)
        allotments_list = list(allotments)
        for a in allotments_list:
            a['day_display'] = day_map.get(a['day'], a['day'])
            a['lab_display'] = lab_map.get(a['LAB'], a['LAB'])
        return JsonResponse({'allotments': allotments_list})
    except Batch.DoesNotExist:
        return JsonResponse({'allotments': []})

def delete_subject_entry_ajax(request, entry_id):
    if request.method == "POST":
        entry = get_object_or_404(SubjectEntry, id=entry_id)
        entry.delete()
        return JsonResponse({"status": "success"})
    return JsonResponse({"status": "error", "message": "Invalid method"}, status=400)

@login_required
def palette_allocate(request, subject_id, staff_id):
    subject = get_object_or_404(SubjectEntry, id=subject_id)
    staff = get_object_or_404(Staff, id=staff_id)
    
    # Check if this exact SubjectEntry is already allotted to this staff
    exists = TimetableEntry.objects.filter(staff=staff, subject=subject).exists()
    if not exists:
        TimetableEntry.objects.create(staff=staff, subject=subject, user=request.user)
    
    return redirect("timetable")

import json
from django.views.decorators.csrf import csrf_exempt
from .models import LabPreference, ParallelSubjectGroup

@login_required
def auto_lab_allotment_view(request):
    dp = get_current_period(request)
    
    # Auto-sync batches from existing SubjectEntries if they were uploaded manually
    existing_entries = SubjectEntry.objects.filter(period=dp)
    for e in existing_entries:
        if e.class_name and e.subject_name:
            b, _ = Batch.objects.get_or_create(name=e.class_name, period=dp)
            BatchSubject.objects.get_or_create(batch=b, subject_name=e.subject_name)
            
    batches = Batch.objects.filter(period=dp).prefetch_related('subjects')
    lab_choices = SubjectEntry.LAB_CHOICES
    
    preferences = LabPreference.objects.filter(period=dp)
    parallel_groups = ParallelSubjectGroup.objects.filter(period=dp)
    
    try:
        sem = Semester.objects.get(name=dp)
        layout_type = sem.layout_type
    except Semester.DoesNotExist:
        layout_type = 'classic'
        
    lab_timetables = {}
    all_subjects = list(SubjectEntry.objects.filter(period=dp))
    
    for lab_code, lab_name in SubjectEntry.LAB_CHOICES:
        slots = []
        days_keys = ["M", "T", "W", "Th", "F"]
        for dkey in days_keys:
            day_labels = get_day_labels(dkey, layout_type)
            row_slots = []
            for lbl in day_labels:
                row_slots.append({"hour_label": lbl, "subject": None})
            slots.append(row_slots)
            
        lab_subjects = [s for s in all_subjects if s.LAB == lab_code]
        
        for sub in lab_subjects:
            hours = [int(x) for x in sub.allotted_hours.split(",")]
            row = {"M": 0, "T": 1, "W": 2, "Th": 3, "F": 4}[sub.day]
            
            adjusted_hours = []
            if layout_type == 'new':
                if sub.day == 'F':
                    for hour in hours:
                        if hour == 8: adjusted_hours.append(6)
                        elif hour == 6: adjusted_hours.append(7)
                        else: adjusted_hours.append(hour)
                else:
                    for hour in hours:
                        adjusted_hours.append(hour)
            else:
                for hour in hours:
                    if hour == 8: adjusted_hours.append(5)
                    elif hour == 5: adjusted_hours.append(6)
                    elif hour == 6: adjusted_hours.append(7)
                    elif hour == 7: adjusted_hours.append(8)
                    else: adjusted_hours.append(hour)
                    
            adj = sorted(set(adjusted_hours))
            if not adj:
                continue
            start = adj[0] - 1
            end = adj[-1] - 1
            
            max_index = 6 if layout_type == 'new' else 7
            if start < 0: start = 0
            if end > max_index: end = max_index
            
            for c in range(start, end + 1):
                if c < len(slots[row]):
                    if c == start:
                        colspan_val = min(end, len(slots[row]) - 1) - start + 1
                        slots[row][c] = {
                            "hour_label": slots[row][c]["hour_label"],
                            "subject": sub.subject_name,
                            "class_name": sub.class_name,
                            "colspan": colspan_val,
                            "height_px": colspan_val * 24,
                        }
                    else:
                        slots[row][c] = None
                        
        lab_timetables[lab_code] = {"timetable_slots": slots, "name": lab_name}
    
    batch_data = []
    for b in batches:
        batch_data.append({
            'id': b.id,
            'name': b.name,
            'subjects': [{'name': s.subject_name, 'hours': s.hours} for s in b.subjects.all()]
        })
        
    semesters = Semester.objects.all().order_by('name')
    current_view_sem = dp

    return render(request, 'auto_lab_allotment.html', {
        'batch_data': json.dumps(batch_data),
        'batches': batches,
        'lab_choices': json.dumps(list(SubjectEntry.LAB_CHOICES)),
        'lab_timetables': lab_timetables,
        'layout_type': layout_type,
        'semesters': semesters,
        'current_view_sem': current_view_sem,
    })

@csrf_exempt
@login_required
def api_run_auto_lab_allotment(request):
    if request.method != "POST":
        return JsonResponse({"error": "Invalid method"}, status=400)
    
    try:
        dp = get_current_period(request)
        data = json.loads(request.body)
        
        selected_batches = data.get('selected_batches', [])
        deselected_subjects = data.get('deselected_subjects', {})
        parallel_groups_data = data.get('parallel_groups', [])
        lab_preferences_data = data.get('lab_preferences', [])
        batch_preferences_data = data.get('batch_preferences', [])
        subject_slots_data = data.get('subject_slots', {})
        subject_labs_data = data.get('subject_labs', {})
        subject_configs = data.get('subject_configs', {})
        global_excluded_labs = data.get('global_excluded_labs', [])
        
        with transaction.atomic():
            LabPreference.objects.filter(period=dp).delete()
            for pref in lab_preferences_data:
                LabPreference.objects.create(
                    lab_name=pref['lab'],
                    allowed_days=pref['days'],
                    allowed_hours=pref['hours'],
                    day_gap=int(pref.get('gap', 1)),
                    period=dp
                )
            
            ParallelSubjectGroup.objects.filter(period=dp).delete()
            for pg in parallel_groups_data:
                try:
                    batch = Batch.objects.get(id=pg['batch_id'])
                    ParallelSubjectGroup.objects.create(
                        batch=batch,
                        subject_1=pg['sub1'],
                        subject_2=pg['sub2'],
                        period=dp
                    )
                except Batch.DoesNotExist:
                    continue
                
            batches_to_allocate = Batch.objects.filter(id__in=selected_batches)
            
            for b in batches_to_allocate:
                SubjectEntry.objects.filter(class_name=b.name, period=dp, is_auto_assigned=True).delete()
                
            DAYS = ['M', 'T', 'W', 'Th', 'F']
            results = []
            unallocated = []
            
            # Map batch_id to preferences
            batch_gaps = {str(b['batch_id']): b['gap'] for b in batch_preferences_data}
            batch_preferred_days = {str(b['batch_id']): b.get('preferred_days', []) for b in batch_preferences_data}
            
            def is_batch_free(batch_obj, day, hours):
                hours_set = set(map(int, hours.split(',')))
                existing = SubjectEntry.objects.filter(class_name=batch_obj.name, period=dp)
                
                # Check overlapping time
                for e in existing.filter(day=day):
                    if set(map(int, e.allotted_hours.split(','))).intersection(hours_set):
                        return False
                        
                # Check batch gap constraint
                gap = batch_gaps.get(str(batch_obj.id), 0)
                if gap > 0:
                    day_idx = DAYS.index(day)
                    for e in existing:
                        e_idx = DAYS.index(e.day)
                        if abs(day_idx - e_idx) <= gap and e_idx != day_idx:
                            return False
                            
                return True
                
            def is_lab_free(lab, day, hours):
                hours_set = set(map(int, hours.split(',')))
                existing = SubjectEntry.objects.filter(LAB=lab, day=day, period=dp)
                for e in existing:
                    if set(map(int, e.allotted_hours.split(','))).intersection(hours_set):
                        return False
                return True
            
            def is_unique_subject_free(sub_name, day, hours):
                sub_upper = sub_name.strip().upper()
                is_chem = sub_upper == 'CH'
                is_phys = sub_upper == 'PHY'
                
                if not (is_chem or is_phys):
                    return True
                    
                hours_set = set(map(int, hours.split(',')))
                existing = SubjectEntry.objects.filter(day=day, period=dp)
                
                for e in existing:
                    e_upper = e.subject_name.strip().upper()
                    e_is_chem = e_upper == 'CH'
                    e_is_phys = e_upper == 'PHY'
                    
                    if (is_chem and e_is_chem) or (is_phys and e_is_phys):
                        if set(map(int, e.allotted_hours.split(','))).intersection(hours_set):
                            return False
                return True
                
            def is_lab_eligible(lab_code, day, block):
                if lab_code in global_excluded_labs:
                    return False
                pref = LabPreference.objects.filter(lab_name=lab_code, period=dp).first()
                if pref:
                    if day not in pref.allowed_days.split(','): return False
                    if block not in pref.allowed_hours.split(','): return False
                return is_lab_free(lab_code, day, block)

            def get_blocks_for_duration(duration):
                if duration == 1:
                    return ['1','2','3','4','5','6','7']
                elif duration == 2:
                    return ['1,2', '3,4', '5,6']
                elif duration == 3:
                    return ['1,2,3', '4,5,6', '5,6,7']
                elif duration >= 4:
                    return ['1,2,3,4', '4,5,6,7']
                return ['1,2,3']

            def get_ordered_blocks_with_alternation(assigned_blocks, default_blocks):
                if not assigned_blocks:
                    return default_blocks
                mornings = sum(1 for b in assigned_blocks if int(b.split(',')[0]) <= 3)
                afternoons = len(assigned_blocks) - mornings
                prefer_morning = afternoons >= mornings
                
                preferred = []
                non_preferred = []
                for b in default_blocks:
                    if b in assigned_blocks: continue
                    is_morning = int(b.split(',')[0]) <= 3
                    if (prefer_morning and is_morning) or (not prefer_morning and not is_morning):
                        preferred.append(b)
                    else:
                        non_preferred.append(b)
                return preferred + non_preferred + [b for b in default_blocks if b in assigned_blocks]
            
            def get_batch_lab_days(batch_name):
                entries = SubjectEntry.objects.filter(class_name=batch_name, period=dp, subject_name__isnull=False)
                return set([e.day for e in entries])
            
            allocated_for_batch = {batch.id: set() for batch in batches_to_allocate}
            
            for phase in ['strict', 'normal']:
                for batch in batches_to_allocate:
                    subjects = [s.subject_name for s in batch.subjects.all()]
                    subject_durations = {s.subject_name: s.hours for s in batch.subjects.all()}
                    desel = deselected_subjects.get(str(batch.id), [])
                    subjects_to_allocate = [s for s in subjects if s not in desel]
                    
                    pgs = ParallelSubjectGroup.objects.filter(batch=batch, period=dp)
                    parallel_pairs = [(pg.subject_1, pg.subject_2) for pg in pgs]
                    
                    # Determine day order for this batch
                    pref_days = batch_preferred_days.get(str(batch.id), [])
                    batch_days = pref_days if pref_days else DAYS
                    
                    for s1, s2 in parallel_pairs:
                        s1_valid = s1 in subjects_to_allocate or s1 in ['CH', 'PHY']
                        s2_valid = s2 in subjects_to_allocate or s2 in ['CH', 'PHY']
                        if not (s1_valid and s2_valid): continue
                        
                        is_strict_s1 = subject_configs.get(str(batch.id), {}).get(s1, {}).get('strictPriority')
                        is_strict_s2 = subject_configs.get(str(batch.id), {}).get(s2, {}).get('strictPriority')
                        is_pair_strict = is_strict_s1 or is_strict_s2
                        
                        if phase == 'strict' and not is_pair_strict: continue
                        if phase == 'normal' and is_pair_strict: continue
                        
                        if s1 in allocated_for_batch[batch.id] or s2 in allocated_for_batch[batch.id]: continue
                        allocated_for_batch[batch.id].add(s1)
                        allocated_for_batch[batch.id].add(s2)
                        
                        pref_labs_s1 = subject_labs_data.get(str(batch.id), {}).get(s1, [])
                        labs_s1 = pref_labs_s1 if pref_labs_s1 else [l[0] for l in SubjectEntry.LAB_CHOICES]
                        if subject_configs.get(str(batch.id), {}).get(s1, {}).get('strictPriority') and pref_labs_s1:
                            labs_s1 = [pref_labs_s1[0]]
                            
                        pref_labs_s2 = subject_labs_data.get(str(batch.id), {}).get(s2, [])
                        labs_s2 = pref_labs_s2 if pref_labs_s2 else [l[0] for l in SubjectEntry.LAB_CHOICES]
                        if subject_configs.get(str(batch.id), {}).get(s2, {}).get('strictPriority') and pref_labs_s2:
                            labs_s2 = [pref_labs_s2[0]]
                            
                        pairs_to_try = []
                        for l1 in labs_s1:
                            if l1 in global_excluded_labs: continue
                            for l2 in labs_s2:
                                if l2 in global_excluded_labs: continue
                                if l1 != l2:
                                    pairs_to_try.append((l1, l2))
                                    
                        slots_needed = max(
                            int(subject_slots_data.get(str(batch.id), {}).get(s1, 1)),
                            int(subject_slots_data.get(str(batch.id), {}).get(s2, 1))
                        )
                        slots_allocated = 0
                        assigned_blocks = set()
                        assigned_days = set()
                        
                        allow_split_s1 = subject_configs.get(str(batch.id), {}).get(s1, {}).get('allowSplitLabs')
                        allow_split_s2 = subject_configs.get(str(batch.id), {}).get(s2, {}).get('allowSplitLabs')
                        has_priority = (len(pref_labs_s1) > 0 or len(pref_labs_s2) > 0) and not (allow_split_s1 or allow_split_s2)
                        
                        s1_ex_times = subject_configs.get(str(batch.id), {}).get(s1, {}).get('excludedTimes', [])
                        s2_ex_times = subject_configs.get(str(batch.id), {}).get(s2, {}).get('excludedTimes', [])
                        
                        global_batch_lab_days = get_batch_lab_days(batch.name)
                        if has_priority:
                            success = False
                            for l1, l2 in pairs_to_try:
                                possible_slots = []
                                assigned_days_sim = set()
                                assigned_blocks_sim = set()
                                
                                for required_gap in [1, 0]:
                                    if len(possible_slots) >= slots_needed: break
                                    for day in batch_days:
                                        if len(possible_slots) >= slots_needed: break
                                        total_occupied_days = global_batch_lab_days.union(assigned_days_sim)
                                        if day in total_occupied_days: continue
                                        
                                        if required_gap == 1 and total_occupied_days:
                                            try:
                                                day_idx = DAYS.index(day)
                                                has_conflict = False
                                                for assigned_day in total_occupied_days:
                                                    assigned_idx = DAYS.index(assigned_day)
                                                    if abs(day_idx - assigned_idx) <= 1:
                                                        has_conflict = True
                                                        break
                                                if has_conflict:
                                                    continue
                                            except ValueError:
                                                pass
                                        
                                        duration = max(subject_durations.get(s1, 1), subject_durations.get(s2, 1))
                                        default_blocks = get_blocks_for_duration(duration)
                                        blocks_to_try = get_ordered_blocks_with_alternation(assigned_blocks_sim, default_blocks)
                                    
                                        for block in blocks_to_try:
                                            if len(possible_slots) >= slots_needed: break
                                            time_key = f"{day}:{block}"
                                            if time_key in s1_ex_times or time_key in s2_ex_times: continue
                                            if not is_batch_free(batch, day, block): continue
                                            if not is_unique_subject_free(s1, day, block) or not is_unique_subject_free(s2, day, block): continue
                                            
                                            if is_lab_eligible(l1, day, block) and is_lab_eligible(l2, day, block):
                                                possible_slots.append((day, block, l1, l2))
                                                assigned_days_sim.add(day)
                                                assigned_blocks_sim.add(block)
                                                break
                                            
                                if len(possible_slots) == slots_needed:
                                    for (d, b, lab1, lab2) in possible_slots:
                                        SubjectEntry.objects.create(subject_name=s1, class_name=batch.name, day=d, allotted_hours=b, LAB=lab1, period=dp, is_auto_assigned=True)
                                        SubjectEntry.objects.create(subject_name=s2, class_name=batch.name, day=d, allotted_hours=b, LAB=lab2, period=dp, is_auto_assigned=True)
                                    slots_allocated = slots_needed
                                    success = True
                                    break
                                    
                            if not success:
                                unallocated.append(f"{batch.name} - {s1}||{s2} (only got 0/{slots_needed} slots)")
                        else:
                            possible_slots = []
                            assigned_days_sim = set()
                            assigned_blocks_sim = set()
                            
                            for l1, l2 in pairs_to_try:
                                if len(possible_slots) >= slots_needed: break
                                for required_gap in [1, 0]:
                                    if len(possible_slots) >= slots_needed: break
                                    for day in batch_days:
                                        if len(possible_slots) >= slots_needed: break
                                        total_occupied_days = global_batch_lab_days.union(assigned_days_sim)
                                        if day in total_occupied_days: continue
                                        
                                        if required_gap == 1 and total_occupied_days:
                                            try:
                                                day_idx = DAYS.index(day)
                                                has_conflict = False
                                                for assigned_day in total_occupied_days:
                                                    assigned_idx = DAYS.index(assigned_day)
                                                    if abs(day_idx - assigned_idx) <= 1:
                                                        has_conflict = True
                                                        break
                                                if has_conflict:
                                                    continue
                                            except ValueError:
                                                pass
                                        
                                        duration = max(subject_durations.get(s1, 1), subject_durations.get(s2, 1))
                                        default_blocks = get_blocks_for_duration(duration)
                                        blocks_to_try = get_ordered_blocks_with_alternation(assigned_blocks_sim, default_blocks)
                                    
                                        for block in blocks_to_try:
                                            if len(possible_slots) >= slots_needed: break
                                            time_key = f"{day}:{block}"
                                            if time_key in s1_ex_times or time_key in s2_ex_times: continue
                                            if not is_batch_free(batch, day, block): continue
                                            if not is_unique_subject_free(s1, day, block) or not is_unique_subject_free(s2, day, block): continue
                                            
                                            if is_lab_eligible(l1, day, block) and is_lab_eligible(l2, day, block):
                                                possible_slots.append((day, block, l1, l2))
                                                assigned_days_sim.add(day)
                                                assigned_blocks_sim.add(block)
                                                break
                                            
                            if len(possible_slots) == slots_needed:
                                for (d, b, lab1, lab2) in possible_slots:
                                    SubjectEntry.objects.create(subject_name=s1, class_name=batch.name, day=d, allotted_hours=b, LAB=lab1, period=dp, is_auto_assigned=True)
                                    SubjectEntry.objects.create(subject_name=s2, class_name=batch.name, day=d, allotted_hours=b, LAB=lab2, period=dp, is_auto_assigned=True)
                                slots_allocated = slots_needed
                            else:
                                unallocated.append(f"{batch.name} - {s1}||{s2} (only got 0/{slots_needed} slots)")
                                    
                    for sub in subjects_to_allocate:
                        if sub in allocated_for_batch[batch.id]: continue
                    
                        is_strict = subject_configs.get(str(batch.id), {}).get(sub, {}).get('strictPriority')
                        if phase == 'strict' and not is_strict: continue
                        if phase == 'normal' and is_strict: continue
                        pref_labs = subject_labs_data.get(str(batch.id), {}).get(sub, [])
                        labs_to_check = pref_labs if pref_labs else [l[0] for l in SubjectEntry.LAB_CHOICES]
                    
                        if subject_configs.get(str(batch.id), {}).get(sub, {}).get('strictPriority') and pref_labs:
                            labs_to_check = [pref_labs[0]]
                        
                        labs_to_check = [l for l in labs_to_check if l not in global_excluded_labs]
                    
                        slots_needed = int(subject_slots_data.get(str(batch.id), {}).get(sub, 1))
                        slots_allocated = 0
                        assigned_blocks = set()
                        assigned_days = set()
                    
                        ex_times = subject_configs.get(str(batch.id), {}).get(sub, {}).get('excludedTimes', [])
                    
                        allow_split = subject_configs.get(str(batch.id), {}).get(sub, {}).get('allowSplitLabs')
                        has_priority = len(pref_labs) > 0 and not allow_split
                    
                        if has_priority:
                            global_batch_lab_days = get_batch_lab_days(batch.name)
                            success = False
                            for target_lab in labs_to_check:
                                possible_slots = []
                                assigned_days_sim = set()
                                assigned_blocks_sim = set()
                            
                                for required_gap in [1, 0]:
                                    if len(possible_slots) >= slots_needed: break
                                    for day in batch_days:
                                        if len(possible_slots) >= slots_needed: break
                                        total_occupied_days = global_batch_lab_days.union(assigned_days_sim)
                                        if day in total_occupied_days: continue
                                    
                                        if required_gap == 1 and total_occupied_days:
                                            try:
                                                day_idx = DAYS.index(day)
                                                has_conflict = False
                                                for assigned_day in total_occupied_days:
                                                    assigned_idx = DAYS.index(assigned_day)
                                                    if abs(day_idx - assigned_idx) <= 1:
                                                        has_conflict = True
                                                        break
                                                if has_conflict:
                                                    continue
                                            except ValueError:
                                                pass
                                    
                                        duration = subject_durations.get(sub, 1)
                                        default_blocks = get_blocks_for_duration(duration)
                                        blocks_to_try = get_ordered_blocks_with_alternation(assigned_blocks_sim, default_blocks)
                                
                                        for block in blocks_to_try:
                                            if len(possible_slots) >= slots_needed: break
                                            time_key = f"{day}:{block}"
                                            if time_key in ex_times: continue
                                            if not is_batch_free(batch, day, block): continue
                                            if not is_unique_subject_free(sub, day, block): continue
                                        
                                            if is_lab_eligible(target_lab, day, block):
                                                possible_slots.append((day, block, target_lab))
                                                assigned_days_sim.add(day)
                                                assigned_blocks_sim.add(block)
                                                break
                                        
                                if len(possible_slots) == slots_needed:
                                    for (d, b, lab) in possible_slots:
                                        SubjectEntry.objects.create(subject_name=sub, class_name=batch.name, day=d, allotted_hours=b, LAB=lab, period=dp, is_auto_assigned=True)
                                    slots_allocated = slots_needed
                                    success = True
                                    break
                                
                            if not success:
                                unallocated.append(f"{batch.name} - {sub} (only got 0/{slots_needed} slots)")
                        else:
                            global_batch_lab_days = get_batch_lab_days(batch.name)
                            possible_slots = []
                            assigned_days_sim = set()
                            assigned_blocks_sim = set()
                        
                            for target_lab in labs_to_check:
                                if len(possible_slots) >= slots_needed: break
                                for required_gap in [1, 0]:
                                    if len(possible_slots) >= slots_needed: break
                                    for day in batch_days:
                                        if len(possible_slots) >= slots_needed: break
                                        total_occupied_days = global_batch_lab_days.union(assigned_days_sim)
                                        if day in total_occupied_days: continue
                                    
                                        if required_gap == 1 and total_occupied_days:
                                            try:
                                                day_idx = DAYS.index(day)
                                                has_conflict = False
                                                for assigned_day in total_occupied_days:
                                                    assigned_idx = DAYS.index(assigned_day)
                                                    if abs(day_idx - assigned_idx) <= 1:
                                                        has_conflict = True
                                                        break
                                                if has_conflict:
                                                    continue
                                            except ValueError:
                                                pass
                                    
                                        duration = subject_durations.get(sub, 1)
                                        default_blocks = get_blocks_for_duration(duration)
                                        blocks_to_try = get_ordered_blocks_with_alternation(assigned_blocks_sim, default_blocks)
                                
                                        for block in blocks_to_try:
                                            if len(possible_slots) >= slots_needed: break
                                            time_key = f"{day}:{block}"
                                            if time_key in ex_times: continue
                                            if not is_batch_free(batch, day, block): continue
                                            if not is_unique_subject_free(sub, day, block): continue
                                        
                                            if is_lab_eligible(target_lab, day, block):
                                                possible_slots.append((day, block, target_lab))
                                                assigned_days_sim.add(day)
                                                assigned_blocks_sim.add(block)
                                                break
                                        
                            if len(possible_slots) == slots_needed:
                                for (d, b, lab) in possible_slots:
                                    SubjectEntry.objects.create(subject_name=sub, class_name=batch.name, day=d, allotted_hours=b, LAB=lab, period=dp, is_auto_assigned=True)
                                slots_allocated = slots_needed
                            else:
                                unallocated.append(f"{batch.name} - {sub} (only got 0/{slots_needed} slots)")
                
                    if phase == 'normal':
                        results.append(f"{batch.name}: {len(subjects_to_allocate)} subjects")
            
            msg = f"Allocated {len(results)} batches. "
            if unallocated:
                msg += f"Could not allocate: {', '.join(unallocated)}"
                return JsonResponse({"status": "partial", "message": msg, "unallocated": unallocated})
                        
        return JsonResponse({"status": "success", "message": msg})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
@login_required
def api_clear_all_allotments(request):
    if request.method != "POST":
        return JsonResponse({"error": "Invalid method"}, status=400)
    
    try:
        dp = get_current_period(request)
        with transaction.atomic():
            SubjectEntry.objects.filter(period=dp, is_auto_assigned=True).delete()
            LabPreference.objects.filter(period=dp).delete()
            ParallelSubjectGroup.objects.filter(period=dp).delete()
            
        return JsonResponse({"status": "success", "message": "All lab allotments and configurations for this semester have been cleared."})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({"error": str(e)}, status=500)

@login_required
def upload_document_view(request):
    from .models import Document, DocumentCategory
    if request.method == "POST":
        name = request.POST.get("name")
        uploaded_file = request.FILES.get("uploaded_file")
        category_id = request.POST.get("category_id")
        
        category = None
        if category_id:
            try:
                category = DocumentCategory.objects.get(id=category_id)
            except DocumentCategory.DoesNotExist:
                pass
                
        if name and uploaded_file:
            doc = Document(
                name=name,
                doc_type="uploaded",
                uploaded_file=uploaded_file,
                category=category,
                user=request.user
            )
            doc.save()
            messages.success(request, f"Document '{name}' uploaded successfully!")
        else:
            messages.error(request, "Failed to upload document. Please provide both name and file.")
    return redirect("dashboard")

@login_required
def create_document_view(request):
    from .models import Document, DocumentCategory
    if request.method == "POST":
        name = request.POST.get("name")
        content_json = request.POST.get("content_json")
        category_id = request.POST.get("category_id")
        
        doc_id = request.POST.get("doc_id")
        
        category = None
        if category_id:
            try:
                category = DocumentCategory.objects.get(id=category_id)
            except DocumentCategory.DoesNotExist:
                pass
                
        if name and content_json:
            if doc_id:
                doc = get_object_or_404(Document, id=doc_id, user=request.user)
                doc.name = name
                doc.content_json = content_json
                doc.category = category
                doc.save()
                messages.success(request, f"Document '{name}' updated successfully!")
            else:
                doc = Document(
                    name=name,
                    doc_type="created",
                    content_json=content_json,
                    category=category,
                    user=request.user
                )
                doc.save()
                messages.success(request, f"Document '{name}' created successfully!")
        else:
            messages.error(request, "Failed to create document. Please provide both name and content.")
            
    next_url = request.POST.get("next")
    if next_url:
        return redirect(next_url)
    return redirect("dashboard")

@login_required
def delete_document_view(request, doc_id):
    from .models import Document
    doc = get_object_or_404(Document, id=doc_id, user=request.user)
    name = doc.name
    if doc.doc_type == "uploaded" and doc.uploaded_file:
        if os.path.exists(doc.uploaded_file.path):
            os.remove(doc.uploaded_file.path)
    doc.delete()
    messages.success(request, f"Document '{name}' deleted successfully!")
    # Keep the user on the repository tab
    return redirect("/dashboard/?tab=repository-pane")

@login_required
def bulk_delete_documents_view(request):
    from .models import Document
    if request.method == "POST":
        doc_ids = request.POST.getlist('doc_ids')
        if doc_ids:
            docs = Document.objects.filter(id__in=doc_ids, user=request.user)
            count = docs.count()
            for doc in docs:
                if doc.doc_type == "uploaded" and doc.uploaded_file:
                    if os.path.exists(doc.uploaded_file.path):
                        os.remove(doc.uploaded_file.path)
            docs.delete()
            messages.success(request, f"{count} documents deleted successfully!")
        else:
            messages.error(request, "No documents were selected for deletion.")
            
    # Redirect back to the Custom Documents tab
    return redirect("/dashboard/?tab=repository-pane")

@login_required
def create_category_view(request):
    from .models import DocumentCategory
    if request.method == "POST":
        name = request.POST.get("category_name")
        if name:
            name_clean = name.strip()
            if name_clean:
                DocumentCategory.objects.get_or_create(name=name_clean)
                messages.success(request, f"Category '{name_clean}' created successfully!")
            else:
                messages.error(request, "Category name cannot be empty.")
        else:
            messages.error(request, "Failed to create category.")
    return redirect("dashboard")

@login_required
def generate_lab_report_view(request):
    from .models import DocumentCategory, LabAllotment
    from datetime import datetime
    
    def parse_date(date_str):
        if not date_str:
            return datetime.min
        try:
            return datetime.strptime(date_str.strip(), "%d-%m-%Y")
        except ValueError:
            try:
                return datetime.strptime(date_str.strip(), "%Y-%m-%d")
            except ValueError:
                return datetime.min

    if request.method == "POST":
        # Form submission to generate report
        lab_name = request.POST.get("lab_name")
        start_date = request.POST.get("start_date", "")
        end_date = request.POST.get("end_date", "")
        report_heading = request.POST.get("report_heading", "").strip()
        heading_style = request.POST.get("heading_style", "new")
        orientation = request.POST.get("orientation", "landscape")
        include_class_name = request.POST.get("include_class_name")  # 'on' if checked or None
        
        default_heading = f"Lab Wise Allotment Report - {lab_name or 'All Labs'}"
        final_heading = report_heading if report_heading else default_heading
        
        allotments_qs = LabAllotment.objects.all()
        if lab_name:
            allotments_qs = allotments_qs.filter(lab_name=lab_name)
            
        allotments = list(allotments_qs)
        
        if start_date:
            sd = parse_date(start_date)
            allotments = [a for a in allotments if parse_date(a.start_date) >= sd]
        if end_date:
            ed = parse_date(end_date)
            allotments = [a for a in allotments if parse_date(a.start_date) <= ed]
            
        allotments.sort(key=lambda a: parse_date(a.start_date))
        
        show_class_col = (include_class_name == "on" or include_class_name == "true")
        if show_class_col:
            headers = ["Sl No", "Event Name", "Class Name", "Date", "Total Hours"]
        else:
            headers = ["Sl No", "Event Name", "Date", "Total Hours"]

        # Build table html
        table_html = "<table class=\"doc-table\" style=\"width: 100%; border-collapse: collapse;\" border=\"1\"><tbody>"
        table_html += "<tr>"
        for th in headers:
            table_html += f"<th style=\"border: 1px solid var(--border-color); padding: 6px 10px; background: #f8fafc;\">{th}</th>"
        table_html += "</tr>"
        
        rows = []
        total_cumulative_hours = 0
        for idx, allotment in enumerate(allotments, start=1):
            hours_list = [h.strip() for h in allotment.hours_allotted.split(',') if h.strip()]
            hours_count = len(hours_list)
            total_cumulative_hours += hours_count
            
            if show_class_col:
                row_data = [str(idx), allotment.subject_name, allotment.class_name or "", allotment.start_date, str(hours_count)]
            else:
                row_data = [str(idx), allotment.subject_name, allotment.start_date, str(hours_count)]

            rows.append(row_data)
            
            table_html += "<tr>"
            for cell in row_data:
                table_html += f"<td style=\"border: 1px solid var(--border-color); padding: 6px 10px;\">{cell}</td>"
            table_html += "</tr>"
            
        # Add cumulative total row
        colspan_val = len(headers) - 1
        if show_class_col:
            rows.append(["", "", "", "Cumulative Total", str(total_cumulative_hours)])
        else:
            rows.append(["", "", "Cumulative Total", str(total_cumulative_hours)])
        
        table_html += "<tr>"
        table_html += f"<td colspan=\"{colspan_val}\" style=\"border: 1px solid var(--border-color); padding: 6px 10px; text-align: right; font-weight: bold;\">Cumulative Total</td>"
        table_html += f"<td style=\"border: 1px solid var(--border-color); padding: 6px 10px; font-weight: bold;\">{total_cumulative_hours}</td>"
        table_html += "</tr>"
        table_html += "</tbody></table>"
        
        # Build document JSON
        blocks = [
            {"type": "h2", "text": final_heading, "tableHtml": None, "style": {"bold": True, "italic": False}},
            {"type": "table", "text": "", "tableHtml": table_html, "headers": headers, "rows": rows, "style": {"bold": False, "italic": False}}
        ]
        
        content_json = json.dumps(blocks)
        
        categories = DocumentCategory.objects.all().order_by("name")
        labs = LabAllotment.objects.values_list('lab_name', flat=True).distinct()
        return render(request, "lab_report_generator.html", {
            "content_json": content_json,
            "categories": categories,
            "labs": labs,
            "generated": True,
            "lab_name": lab_name,
            "start_date": start_date,
            "end_date": end_date,
            "report_heading": report_heading,
            "final_heading": final_heading,
            "heading_style": heading_style,
            "orientation": orientation,
            "include_class_name": include_class_name
        })

    # GET request
    categories = DocumentCategory.objects.all().order_by("name")
    labs = LabAllotment.objects.values_list('lab_name', flat=True).distinct()
    
    return render(request, "lab_report_generator.html", {
        "categories": categories,
        "labs": labs,
        "generated": False,
        "include_class_name": "on"
    })


@login_required
def download_lab_report_excel(request):
    from .models import LabAllotment
    from datetime import datetime
    import xlsxwriter
    import io
    
    def parse_date(date_str):
        if not date_str:
            return datetime.min
        try:
            return datetime.strptime(date_str.strip(), "%d-%m-%Y")
        except ValueError:
            try:
                return datetime.strptime(date_str.strip(), "%Y-%m-%d")
            except ValueError:
                return datetime.min

    if request.method == "POST":
        lab_name = request.POST.get("lab_name")
        start_date = request.POST.get("start_date", "")
        end_date = request.POST.get("end_date", "")
        report_heading = request.POST.get("report_heading", "").strip()
        heading_style = request.POST.get("heading_style", "new")
        orientation = request.POST.get("orientation", "landscape")
        include_class_name = request.POST.get("include_class_name")
        
        default_heading = f"Lab Wise Allotment Report - {lab_name or 'All Labs'}"
        final_heading = report_heading if report_heading else default_heading
        
        allotments_qs = LabAllotment.objects.all()
        if lab_name:
            allotments_qs = allotments_qs.filter(lab_name=lab_name)
            
        allotments = list(allotments_qs)
        
        if start_date:
            sd = parse_date(start_date)
            allotments = [a for a in allotments if parse_date(a.start_date) >= sd]
        if end_date:
            ed = parse_date(end_date)
            allotments = [a for a in allotments if parse_date(a.start_date) <= ed]
            
        allotments.sort(key=lambda a: parse_date(a.start_date))

        show_class_col = (include_class_name == "on" or include_class_name == "true")
        if show_class_col:
            headers = ["Sl No", "Event Name", "Class Name", "Date", "Total Hours"]
        else:
            headers = ["Sl No", "Event Name", "Date", "Total Hours"]
        
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output)
        worksheet = workbook.add_worksheet("Lab Report")
        
        title_fmt = workbook.add_format({"bold": True, "font_size": 14, "align": "center"})
        header_fmt = workbook.add_format({"bold": True, "border": 1, "bg_color": "#f8fafc"})
        cell_fmt = workbook.add_format({"border": 1})
        bold_cell_fmt = workbook.add_format({"border": 1, "bold": True})
        
        institute_fmt = workbook.add_format({
            "text_wrap": True, "bold": True, "font_size": 16,
            "align": "center", "valign": "vcenter"
        })
        address_fmt = workbook.add_format({
            "bold": True, "font_size": 11, "align": "center"
        })
        fisat_fmt = workbook.add_format({
            "bold": True, "font_size": 24, "font_name": "Times New Roman",
            "font_color": "#2e3192", "align": "center", "valign": "vcenter"
        })
        sub_fmt = workbook.add_format({
            "bold": True, "font_size": 12, "font_name": "Times New Roman",
            "font_color": "#2e3192", "align": "center", "valign": "vcenter"
        })
        auto_fmt = workbook.add_format({
            "bold": True, "font_size": 11, "font_name": "Arial",
            "font_color": "#f26522", "align": "center", "valign": "vcenter"
        })
        
        worksheet.set_paper(9)
        if orientation == "portrait":
            worksheet.set_portrait()
        else:
            worksheet.set_landscape()
            
        # Draw Logo
        import os
        logo_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "fisat_logo.png")
        try:
            worksheet.insert_image("A1", logo_path, {"x_scale": 0.3, "y_scale": 0.3})
        except:
            pass

        last_col_letter = chr(ord('A') + len(headers) - 1)
        start_row = 5
        if heading_style == "pdf":
            worksheet.merge_range(f"B1:{last_col_letter}1", "FISAT®", fisat_fmt)
            worksheet.merge_range(f"B2:{last_col_letter}2", "FEDERAL INSTITUTE OF SCIENCE AND TECHNOLOGY", sub_fmt)
            worksheet.merge_range(f"B3:{last_col_letter}3", "AUTONOMOUS", auto_fmt)
            worksheet.merge_range(f"A4:{last_col_letter}4", final_heading, title_fmt)
            start_row = 5
        elif heading_style == "old":
            worksheet.merge_range(f"B1:{last_col_letter}1", "Federal Institute of Science And Technology(FISAT)", institute_fmt)
            worksheet.merge_range(f"B2:{last_col_letter}2", "Hormis Nagar,Angamaly", address_fmt)
            worksheet.merge_range(f"B3:{last_col_letter}3", "Department Of Computer Science And Engineering", address_fmt)
            worksheet.merge_range(f"A4:{last_col_letter}4", final_heading, title_fmt)
            start_row = 5
        else:
            worksheet.merge_range(f"A1:{last_col_letter}1", f"FEDERAL INSTITUTE OF SCIENCE AND TECHNOLOGY (FISAT)\n(Hormis Nagar, Mookkannoor, Angamaly, Kerala – 683577)\n{final_heading}", institute_fmt)
            worksheet.set_row(0, 60)
            start_row = 2

        for col_num, header in enumerate(headers):
            worksheet.write(start_row, col_num, header, header_fmt)
            
        if show_class_col:
            worksheet.set_column(0, 0, 8)   # Sl No
            worksheet.set_column(1, 1, 30)  # Event Name
            worksheet.set_column(2, 2, 15)  # Class Name
            worksheet.set_column(3, 3, 15)  # Date
            worksheet.set_column(4, 4, 12)  # Total Hours
        else:
            worksheet.set_column(0, 0, 8)   # Sl No
            worksheet.set_column(1, 1, 40)  # Event Name
            worksheet.set_column(2, 2, 15)  # Date
            worksheet.set_column(3, 3, 12)  # Total Hours
        
        row_num = start_row + 1
        total_cumulative_hours = 0
        for idx, allotment in enumerate(allotments, start=1):
            hours_list = [h.strip() for h in allotment.hours_allotted.split(',') if h.strip()]
            hours_count = len(hours_list)
            total_cumulative_hours += hours_count
            
            if show_class_col:
                worksheet.write(row_num, 0, idx, cell_fmt)
                worksheet.write(row_num, 1, allotment.subject_name, cell_fmt)
                worksheet.write(row_num, 2, allotment.class_name or "", cell_fmt)
                worksheet.write(row_num, 3, allotment.start_date, cell_fmt)
                worksheet.write(row_num, 4, hours_count, cell_fmt)
            else:
                worksheet.write(row_num, 0, idx, cell_fmt)
                worksheet.write(row_num, 1, allotment.subject_name, cell_fmt)
                worksheet.write(row_num, 2, allotment.start_date, cell_fmt)
                worksheet.write(row_num, 3, hours_count, cell_fmt)

            row_num += 1
            
        total_col_idx = len(headers) - 1
        prev_col_letter = chr(ord('A') + total_col_idx - 1)
        worksheet.merge_range(f"A{row_num+1}:{prev_col_letter}{row_num+1}", "Cumulative Total", bold_cell_fmt)
        worksheet.write(row_num, total_col_idx, total_cumulative_hours, bold_cell_fmt)
            
        worksheet.merge_range(f"A{row_num+1}:C{row_num+1}", "Cumulative Total", bold_cell_fmt)
        worksheet.write(row_num, 3, total_cumulative_hours, bold_cell_fmt)
        
        workbook.close()
        output.seek(0)
        
        response = HttpResponse(
            output,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        safe_filename = final_heading.replace(" ", "_").replace("/", "_")
        response["Content-Disposition"] = f'attachment; filename="{safe_filename}.xlsx"'
        return response
    
    return redirect("generate_lab_report")

@login_required
def lab_system_configuration_view(request):
    """
    Renders the Lab System Configuration generator interface.
    """
    from .models import DocumentCategory
    category, created = DocumentCategory.objects.get_or_create(name="Lab System Configuration")
    return render(request, 'lab_system_configuration.html', {'category': category})


@login_required
def download_custom_document_excel(request, doc_id):
    from .models import Document
    import json
    import xlsxwriter
    import io
    
    doc = get_object_or_404(Document, pk=doc_id)
    
    heading_style = request.GET.get("heading", "pdf")
    orientation = request.GET.get("orientation", "portrait")
    
    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output)
    worksheet = workbook.add_worksheet("Document")
    
    worksheet.set_paper(9)
    if orientation == "portrait":
        worksheet.set_portrait()
    else:
        worksheet.set_landscape()
        
    institute_fmt = workbook.add_format({
        "text_wrap": True, "bold": True, "font_size": 16,
        "align": "center", "valign": "vcenter"
    })
    address_fmt = workbook.add_format({
        "bold": True, "font_size": 11, "align": "center"
    })
    fisat_fmt = workbook.add_format({
        "bold": True, "font_size": 24, "font_name": "Times New Roman",
        "font_color": "#2e3192", "align": "center", "valign": "vcenter"
    })
    sub_fmt = workbook.add_format({
        "bold": True, "font_size": 12, "font_name": "Times New Roman",
        "font_color": "#2e3192", "align": "center", "valign": "vcenter"
    })
    auto_fmt = workbook.add_format({
        "bold": True, "font_size": 11, "font_name": "Arial",
        "font_color": "#f26522", "align": "center", "valign": "vcenter"
    })
    title_fmt = workbook.add_format({"bold": True, "font_size": 14, "align": "center"})
    
    # Generic format for content
    h2_fmt = workbook.add_format({"bold": True, "font_size": 14, "bg_color": "#302683", "font_color": "white", "align": "center"})
    h3_fmt = workbook.add_format({"bold": True, "font_size": 12, "font_color": "#334155"})
    h4_fmt = workbook.add_format({"bold": True, "font_size": 11, "font_color": "#475569"})
    body_fmt = workbook.add_format({"font_size": 11, "font_color": "#334155", "text_wrap": True})
    
    table_header_fmt = workbook.add_format({"bold": True, "border": 1, "bg_color": "#f8fafc"})
    table_cell_fmt = workbook.add_format({"border": 1})
    
    start_row = 0
    max_col = 6
    
    # Draw Logo
    import os
    logo_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "fisat_logo.png")
    try:
        worksheet.insert_image("A1", logo_path, {"x_scale": 0.3, "y_scale": 0.3})
    except:
        pass

    if heading_style == "pdf":
        worksheet.merge_range(f"B1:G1", "FISAT®", fisat_fmt)
        worksheet.merge_range(f"B2:G2", "FEDERAL INSTITUTE OF SCIENCE AND TECHNOLOGY", sub_fmt)
        worksheet.merge_range(f"B3:G3", "AUTONOMOUS", auto_fmt)
        start_row = 4
    elif heading_style == "old":
        worksheet.merge_range(f"B1:G1", "Federal Institute of Science And Technology(FISAT)", institute_fmt)
        worksheet.merge_range(f"B2:G2", "Hormis Nagar,Angamaly", address_fmt)
        worksheet.merge_range(f"B3:G3", "Department Of Computer Science And Engineering", address_fmt)
        start_row = 4
    else:
        worksheet.merge_range(f"A1:G1", "FEDERAL INSTITUTE OF SCIENCE AND TECHNOLOGY (FISAT)\n(Hormis Nagar, Mookkannoor, Angamaly, Kerala – 683577)", institute_fmt)
        worksheet.set_row(0, 45)
        start_row = 2
        
    try:
        blocks = json.loads(doc.content_json)
    except:
        blocks = []
        
    row_num = start_row
    
    worksheet.set_column(0, 6, 12)
    
    for block in blocks:
        btype = block.get("type", "body")
        text = block.get("text", "")
        
        if btype == "table":
            headers = block.get("headers", [])
            rows = block.get("rows", [])
            
            # Set col widths dynamically for table if it fits
            for i, h in enumerate(headers):
                if i < 7:
                    # Give more width to 2nd col usually for "Event Name" or "Details"
                    if i == 1:
                        worksheet.set_column(i, i, 40)
                    else:
                        worksheet.set_column(i, i, max(12, len(str(h)) + 2))
                        
            for i, h in enumerate(headers):
                if i <= 6:
                    worksheet.write(row_num, i, h, table_header_fmt)
            row_num += 1
            
            for r in rows:
                for i, cell in enumerate(r):
                    if i <= 6:
                        worksheet.write(row_num, i, cell, table_cell_fmt)
                row_num += 1
            row_num += 1
            
        elif btype == "h2":
            worksheet.merge_range(f"A{row_num+1}:G{row_num+1}", text, h2_fmt)
            row_num += 2
        elif btype == "h3":
            worksheet.merge_range(f"A{row_num+1}:G{row_num+1}", text, h3_fmt)
            row_num += 1
        elif btype == "h4":
            worksheet.merge_range(f"A{row_num+1}:G{row_num+1}", text, h4_fmt)
            row_num += 1
        else:
            # Body, bullet, number
            if btype == "bullet":
                text = "• " + text
            elif btype == "number":
                # simplistic number fallback
                text = "- " + text
                
            worksheet.merge_range(f"A{row_num+1}:G{row_num+1}", text, body_fmt)
            row_num += 1

    workbook.close()
    output.seek(0)
    
    from django.http import HttpResponse
    response = HttpResponse(
        output,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    safe_filename = doc.name.replace(" ", "_").replace("/", "_")
    response["Content-Disposition"] = f'attachment; filename="{safe_filename}.xlsx"'
    return response

