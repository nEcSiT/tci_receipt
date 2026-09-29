#!/usr/bin/env bash
set -e

echo "Running database migrations..."
python manage.py migrate --noinput

echo "Seeding initial system data..."
python manage.py seed_db

exec "$@"
