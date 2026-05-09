#!/usr/bin/env bash
#
# cleanup.sh — replay DELETE requests from your test log file
#
# Usage:
#   ./cleanup.sh /path/to/your/test.log
#
# It issues each DELETE against $BASE_URL and continues on any failure.
#

#------------------------------------------------------------------------------
# 1) Configuration
#------------------------------------------------------------------------------
BASE_URL="http://localhost:7999"

# do not abort on curl failures
set +e

#------------------------------------------------------------------------------
# 2) Sanity check / usage
#------------------------------------------------------------------------------
if [[ $# -ne 1 ]]; then
  cat <<EOF
Usage: $0 /path/to/logfile

Reads the JSON log (one object per line), filters for entries
with "delete_method":"DELETE", and replays each DELETE via curl
against \$BASE_URL, continuing on errors.
EOF
  exit 1
fi

LOGFILE="$1"
if [[ ! -r "$LOGFILE" ]]; then
  echo "error: cannot read '$LOGFILE'"
  exit 2
fi

#------------------------------------------------------------------------------
# 3) Extract DELETE entries and fire curl for each
#------------------------------------------------------------------------------
#
# We use jq to pick only the objects whose delete_method is DELETE,
# then emit lines of the form:
#
#   DELETE /api/… <body‑JSON‑or‑null>
#
# and finally shell‐parse each line to build & run the curl command.
#
# Note: curl runs with -s (silent) and || true so the loop continues
# even if an individual DELETE fails.
#
jq -cr '
  select(.delete_method == "DELETE")
  | "\(.delete_method) \(.delete_uri) \((.delete_body // null) | @json)"' \
  "$LOGFILE" \
| while IFS= read -r line; do
    # Split into METHOD, URI and BODY (JSON or literal null)
    method="${line%% *}"
    rest="${line#* }"
    uri="${rest%% *}"
    body="${rest#* }"

    # Build base curl command
    cmd=(curl -s -X "$method" "$BASE_URL$uri" -H "Content-Type: application/json")

    # Only add -d when there is a non-null body
    if [[ "$body" != null ]]; then
      cmd+=(-d "$body")
    fi

    # Echo the request and execute (|| true to never abort)
    printf '>>> %s %s %s\n' "$method" "$BASE_URL$uri" "${body:0:80}..."
    "${cmd[@]}" || true
done

echo "Done."