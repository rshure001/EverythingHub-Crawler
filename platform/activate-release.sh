#!/usr/bin/env sh
set -eu

# Usage: sudo sh platform/activate-release.sh /srv/everythinghub/releases/<release-id>
release_dir="${1:-}"
if [ -z "$release_dir" ]; then
  echo "A release directory is required" >&2
  exit 2
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
resolved_release="$(python3 "$SCRIPT_DIR/resolve_release.py" "$release_dir")" || exit 2

install -d -m 0755 /srv/everythinghub
ln -sfn "$resolved_release" /srv/everythinghub/current.next
mv -Tf /srv/everythinghub/current.next /srv/everythinghub/current
printf 'Activated release: %s\n' "$resolved_release"
