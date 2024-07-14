# Generated by Django 5.0.2 on 2024-07-14 02:11

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='Staff',
            fields=[
                ('_id', models.AutoField(db_column='sid', primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=100)),
            ],
            options={
                'db_table': 'staff',
            },
        ),
        migrations.CreateModel(
            name='SubjectEntry',
            fields=[
                ('_id', models.AutoField(db_column='tid', primary_key=True, serialize=False)),
                ('subject_name', models.CharField(max_length=100)),
                ('class_name', models.CharField(max_length=100)),
                ('day', models.CharField(choices=[('M', 'Monday'), ('T', 'Tuesday'), ('W', 'Wednesday'), ('Th', 'Thursday'), ('F', 'Friday')], max_length=2)),
                ('allotted_hours', models.CharField(max_length=10)),
            ],
            options={
                'db_table': 'subjectentry',
            },
        ),
        migrations.CreateModel(
            name='TimetableEntry',
            fields=[
                ('_id', models.AutoField(db_column='tid', primary_key=True, serialize=False)),
                ('staff', models.ForeignKey(db_column='staffid', on_delete=django.db.models.deletion.CASCADE, to='fisat.staff')),
                ('subject', models.ForeignKey(db_column='subjectid', on_delete=django.db.models.deletion.CASCADE, to='fisat.subjectentry')),
            ],
            options={
                'db_table': 'timetableentry',
                'unique_together': {('staff', 'subject')},
            },
        ),
    ]
