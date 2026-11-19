#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
[[ -f "$ENV_FILE" ]] || { echo "Missing $ENV_FILE" >&2; exit 1; }

mapfile -t hosts < <(python3 - "$ENV_FILE" <<'PY'
import sys
values = {}
for raw in open(sys.argv[1], encoding="utf-8"):
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    values[key.strip()] = value.strip().strip('"').strip("'")
for key in ("GIT_HOST", "WEB_HOST"):
    print(values.get(key, ""))
PY
)
GIT_HOST="${hosts[0]:-}"
WEB_HOST="${hosts[1]:-}"
[[ -n "$GIT_HOST" && -n "$WEB_HOST" ]] || { echo "GIT_HOST and WEB_HOST must be set" >&2; exit 1; }

echo "== Container status =="
docker compose -p everythinghub-host --env-file "$ENV_FILE" -f "$ROOT_DIR/platform/compose.yml" ps

echo "\n== Public HTTPS checks =="
curl --fail --silent --show-error --max-time 15 --output /dev/null --write-out "Website: HTTP %{http_code}, TLS %{ssl_verify_result}\n" "https://$WEB_HOST/"
curl --fail --silent --show-error --max-time 15 --output /dev/null --write-out "Git service: HTTP %{http_code}, TLS %{ssl_verify_result}\n" "https://$GIT_HOST/"

if [[ -L /srv/everythinghub/current ]]; then
  printf '\nActive release: '
  readlink /srv/everythinghub/current
else
  echo "\nNo active website release symlink exists yet."
  exit 1
fi
