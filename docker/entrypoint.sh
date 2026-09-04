#!/bin/sh
set -eu
mkdir -p "${DATA_DIR:-/data}" "${DATA_DIR:-/data}/uploads"
python manage.py migrate --noinput
python manage.py bootstrap_admin
exec gunicorn tracker.wsgi:application --bind 0.0.0.0:8000 --workers 1 --threads 4 --timeout 120 --access-logfile -
