#!/usr/bin/env sh
set -eu

# Usage: sudo sh platform/activate-release.sh /srv/everythinghub/releases/<release-id>
release_dir="${1:-}"
case "$release_dir" in
  /srv/everythinghub/releases/*) ;;
  *) echo "Refusing release path outside /srv/everythinghub/releases" >&2; exit 2 ;;
esac

if [ ! -d "$release_dir" ] || [ ! -f "$release_dir/index.html" ]; then
  echo "Release must be a directory containing index.html" >&2
  exit 3
fi

install -d -m 0755 /srv/everythinghub
ln -sfn "$release_dir" /srv/everythinghub/current.next
mv -Tf /srv/everythinghub/current.next /srv/everythinghub/current
printf 'Activated release: %s\n' "$release_dir"
