#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
MIN_FREE_GB="${MIN_FREE_GB:-10}"

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
pass() { printf 'OK: %s\n' "$*"; }

[[ -f "$ENV_FILE" ]] || fail "Missing $ENV_FILE (copy platform/.env.example and configure it privately)"
command -v docker >/dev/null 2>&1 || fail "Docker is not installed"
docker compose version >/dev/null 2>&1 || fail "Docker Compose plugin is unavailable"
docker info >/dev/null 2>&1 || fail "Docker daemon is not reachable"
pass "Docker Engine and Compose are available"

docker compose --env-file "$ENV_FILE" -f "$ROOT_DIR/platform/compose.yml" config -q || fail "Compose configuration is invalid"
pass "Compose configuration is valid"

free_kb="$(df -Pk / | awk 'NR==2 {print $4}')"
required_kb="$((MIN_FREE_GB * 1024 * 1024))"
(( free_kb >= required_kb )) || fail "Less than ${MIN_FREE_GB} GB free on root filesystem"
pass "Disk space meets minimum ${MIN_FREE_GB} GB threshold"

mem_kb="$(awk '/MemTotal/ {print $2}' /proc/meminfo)"
(( mem_kb >= 2097152 )) || fail "At least 2 GiB RAM is required for the initial stack"
pass "Memory meets minimum 2 GiB threshold"

# Parse the dotenv file without sourcing or executing its contents.
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
[[ -n "$GIT_HOST" && -n "$WEB_HOST" ]] || fail "GIT_HOST and WEB_HOST must be set in the environment file"
for host in "$GIT_HOST" "$WEB_HOST"; do
  getent ahosts "$host" >/dev/null 2>&1 || fail "Hostname does not resolve: $host"
  pass "DNS resolves: $host"
done

printf '\nPreflight only: no firewall, DNS, services, or production data were changed.\n'
