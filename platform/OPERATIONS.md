# Everything Hub Hosting Operations

## First installation

Run only on the Linux machine that Everything Hub controls:

```bash
sudo bash platform/install-host.sh
```

The installer runs preflight checks, creates the release directory, pulls the pinned service images, and starts the stack. It does not edit DNS or firewall rules. DNS must already resolve to the host and inbound 80/443 plus Git SSH 2222 must be deliberately configured before public verification.

## Health check

```bash
bash platform/host-status.sh
```

This checks Compose service state, public HTTPS responses, certificate validation, and the active release pointer.

## Publish and rollback

Build a release outside the live directory, ensure it contains `index.html`, and place it under `/srv/everythinghub/releases/<release-id>`. Activate it:

```bash
sudo bash platform/activate-release.sh /srv/everythinghub/releases/<release-id>
```

Rollback by activating the previous known-good release using the same command.

## Backup

```bash
sudo bash platform/backup-host.sh
```

The backup contains Forgejo's persistent volume and published release archives. Copy backups off-host to a separate encrypted destination and test restoration. A local backup alone is not disaster recovery.

## Incident response

- If the new release fails, atomically reactivate the last known-good release.
- If Forgejo is unhealthy, preserve its data volume and inspect container logs before attempting repair.
- Never delete or recreate data volumes as a troubleshooting shortcut.
- Do not run database resets, production imports, or destructive recovery against crawler or unrelated project data.
- Record the last known-good release ID and backup checksum before a maintenance window.
