#!/bin/sh
set -e

# Retries briefly in case the (external, e.g. Supabase) database isn't
# accepting connections yet on the very first boot of the stack.
attempt=0
max_attempts=10
until alembic upgrade head; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge "$max_attempts" ]; then
        echo "alembic upgrade head failed after ${max_attempts} attempts - refusing to start with an unmigrated schema." >&2
        exit 1
    fi
    echo "Database not ready yet (attempt ${attempt}/${max_attempts}) - retrying in 3s..." >&2
    sleep 3
done

# A single worker is intentional, not a placeholder: services/realtime.py
# and the /notifications/stream SSE fan-out keep connected-client state
# in this process's memory. Multiple uvicorn workers would each have an
# isolated copy, so a notification published while a client is attached
# to a different worker would silently never reach them. Scale this
# service horizontally (multiple containers behind a load balancer with
# sticky sessions) rather than via --workers if more throughput is ever
# needed.
exec uvicorn server:app --host 0.0.0.0 --port 8000
