import os
import sys

# Add the project directory to the sys.path
sys.path.insert(0, '/home/varun/programs/fisatlab')

# Activate the virtual environment
activate_this = '/home/varun/timetable/bin/activate_this.py'
with open(activate_this) as file_:
    exec(file_.read(), dict(__file__=activate_this))

# Set environment variables
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'fisatlab.settings')

# Import Django
from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()

