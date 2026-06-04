#!/bin/bash
# Exit on any error
set -e

# ==========================================
# Configuration Variables
# ==========================================
PROJECT_NAME="timetable"
APP_NAME="fisatlab"
DB_NAME="fisatdb"
DB_USER="fisat"
DB_PASS="vinayaka"

# The system user that will run the app
USER="varun"
# The path where the project will live
PROJECT_DIR="/home/$USER/$PROJECT_NAME"

echo "=========================================="
echo "Starting Server Deployment Setup for $PROJECT_NAME"
echo "=========================================="

# 1. Update system and install required dependencies
echo ">>> Updating system packages and installing dependencies..."
sudo apt update
sudo apt install -y python3-pip python3-venv python3-dev libpq-dev postgresql postgresql-contrib nginx curl git

# 2. Set up PostgreSQL Database
echo ">>> Configuring PostgreSQL Database..."
sudo -u postgres psql -c "CREATE DATABASE $DB_NAME;" || echo "Database already exists"
sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';" || sudo -u postgres psql -c "ALTER USER $DB_USER WITH PASSWORD '$DB_PASS';"
sudo -u postgres psql -c "ALTER ROLE $DB_USER SET client_encoding TO 'utf8';"
sudo -u postgres psql -c "ALTER ROLE $DB_USER SET default_transaction_isolation TO 'read committed';"
sudo -u postgres psql -c "ALTER ROLE $DB_USER SET timezone TO 'UTC';"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;"

# 3. Create project directory
echo ">>> Ensuring project directory exists..."
mkdir -p $PROJECT_DIR
cd $PROJECT_DIR

# 4. Set up Virtual Environment and Python Dependencies
echo ">>> Setting up Python virtual environment..."
python3 -m venv venv
source venv/bin/activate

echo ">>> Installing Python packages..."
# Installing the required dependencies based on the project's settings
pip install django psycopg2-binary django-cors-headers social-auth-app-django XlsxWriter requests gunicorn

# NOTE: At this point, you must make sure your Django code is uploaded to $PROJECT_DIR
# If the code isn't uploaded yet, the following steps will fail.
# You can clone it via Git:
# git clone <your-repo-url> .

# 5. Gunicorn Systemd Service
echo ">>> Creating Gunicorn systemd service file..."
sudo tee /etc/systemd/system/gunicorn.service > /dev/null <<EOF
[Unit]
Description=gunicorn daemon for $PROJECT_NAME
After=network.target

[Service]
User=$USER
Group=www-data
WorkingDirectory=$PROJECT_DIR
ExecStart=$PROJECT_DIR/venv/bin/gunicorn \
          --access-logfile - \
          --workers 3 \
          --bind unix:$PROJECT_DIR/$PROJECT_NAME.sock \
          $APP_NAME.wsgi:application

[Install]
WantedBy=multi-user.target
EOF

# 6. Nginx Configuration
echo ">>> Creating Nginx server block..."
SERVER_IP="13.61.176.252"

sudo tee /etc/nginx/sites-available/$PROJECT_NAME > /dev/null <<EOF
server {
    listen 80;
    server_name \$SERVER_IP;

    location = /favicon.ico { access_log off; log_not_found off; }
    
    # Static files routing
    location /static/ {
        root $PROJECT_DIR;
    }
    
    # Media files routing (if applicable)
    location /media/ {
        root $PROJECT_DIR;
    }

    # Pass all other requests to Gunicorn
    location / {
        include proxy_params;
        proxy_pass http://unix:$PROJECT_DIR/$PROJECT_NAME.sock;
    }
}
EOF

# Enable the Nginx configuration
echo ">>> Enabling Nginx configuration..."
sudo ln -sf /etc/nginx/sites-available/$PROJECT_NAME /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default

# Restart services
echo ">>> Starting Gunicorn and restarting Nginx..."
sudo systemctl daemon-reload
sudo systemctl start gunicorn
sudo systemctl enable gunicorn
sudo systemctl restart nginx

echo "=========================================="
echo "Deployment setup script completed!"
echo "Make sure your Django project files are inside $PROJECT_DIR"
echo "Then, run migrations and collectstatic inside the virtual environment:"
echo "  cd $PROJECT_DIR"
echo "  source venv/bin/activate"
echo "  python manage.py migrate"
echo "  python manage.py collectstatic --noinput"
echo "  sudo systemctl restart gunicorn"
echo "=========================================="
