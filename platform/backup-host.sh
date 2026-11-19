#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_ROOT="${BACKUP_ROOT:-/var/backups/everythinghub}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="$BACKUP_ROOT/$STAMP"

command -v docker >/dev/null 2>&1 || { echo "Docker is required" >&2; exit 1; }
docker volume inspect "${FORGEJO_VOLUME:-forgejo_data}" >/dev/null 2>&1 || {
  echo "Forgejo volume not found; refusing incomplete backup" >&2; exit 1;
}
[[ -d /srv/everythinghub/releases ]] || {
  echo "Release directory not found; refusing incomplete backup" >&2; exit 1;
}

umask 077
mkdir -p "$DEST"
chmod 700 "$BACKUP_ROOT" "$DEST"

volume="${FORGEJO_VOLUME:-forgejo_data}"
docker run --rm -v "$volume:/source:ro" -v "$DEST:/backup" alpine:3.20 \
  sh -c 'cd /source && tar czf /backup/forgejo-data.tar.gz .'
tar -C /srv/everythinghub -czf "$DEST/site-releases.tar.gz" releases
if [[ -L /srv/everythinghub/current ]]; then
  readlink /srv/everythinghub/current > "$DEST/current-release.txt"
fi
(
  cd "$DEST"
  sha256sum ./*.tar.gz > SHA256SUMS
)
chmod -R go-rwx "$DEST"
printf 'Backup created: %s\n' "$DEST"
printf 'Copy this backup to a separate, encrypted location before relying on it for disaster recovery.\n'
