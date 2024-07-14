# Generated by Django 5.0.2 on 2024-07-14 16:27

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('fisat', '0002_subjectentry_lab'),
    ]

    operations = [
        migrations.AlterField(
            model_name='subjectentry',
            name='LAB',
            field=models.CharField(choices=[('L1', 'CCF L1'), ('L2', 'CCF L2'), ('L3', 'CCF L3'), ('L4', 'CCF L4'), ('L5', 'CCF L5'), ('L6', 'CCF L6'), ('L7', 'CCF L7'), ('L8', 'CCF L8'), ('L9', 'CCF L9'), ('MP LAB', 'MICRO PROCESSOR LAB'), ('PG LAB', 'PG LAB')], max_length=50),
        ),
    ]