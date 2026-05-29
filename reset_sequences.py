from django.core.management import call_command
from django.db import connection
import io

out = io.StringIO()
call_command('sqlsequencereset', 'fisat', stdout=out)
sql = out.getvalue()

statements = [s.strip() for s in sql.split(';') if s.strip()]

with connection.cursor() as cursor:
    for statement in statements:
        if statement.upper() in ('BEGIN', 'COMMIT'):
            continue
        print(f"Executing: {statement}")
        cursor.execute(statement)
print("Sequences reset successfully!")
