#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
COMPOSE=(docker compose -p everythinghub-host --env-file "$ENV_FILE" -f "$ROOT_DIR/platform/compose.yml")

if [[ "$EUID" -ne 0 ]]; then
  echo "Run this installer with sudo on the host you control." >&2
  exit 1
fi
[[ -f "$ENV_FILE" ]] || {
  echo "Missing $ENV_FILE. Copy platform/.env.example to .env and configure it privately." >&2
  exit 1
}

bash "$ROOT_DIR/platform/host-preflight.sh"
install -d -m 0755 /srv/everythinghub/releases
"${COMPOSE[@]}" pull
"${COMPOSE[@]}" up -d
"${COMPOSE[@]}" ps

printf '\nServices have been started. This installer did not change DNS or firewall rules.\n'
printf 'Verify both hostnames externally before importing production repositories or directing traffic.\n'
