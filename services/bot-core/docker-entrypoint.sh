#!/bin/bash
# --- Permission normalization on the bind-mounted data dir (runs as ROOT, before anything else) ---
set -euo pipefail
mkdir -p /app/data
chown -R botuser:botuser /app/data
chmod -R u+rwX /app/data
set +euo pipefail
# --- Drop to the non-root runtime user for the actual app ---
exec gosu botuser /bin/bash -c 'source /opt/venv/bin/activate && cd /app/src && /opt/venv/bin/python -m persist.database.run_migration upgrade && /opt/venv/bin/python /app/src/main.py & tail -f /dev/null'
