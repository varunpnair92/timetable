from django.core.management import call_command
from django.db import connection
import io

out = io.StringIO()
call_command('sqlsequencereset', 'fisat', stdout=out)
sql = out.getvalue()
print("Executing SQL to reset sequences:")
print(sql)
with connection.cursor() as cursor:
    cursor.execute(sql)
print("Sequences reset successfully!")
