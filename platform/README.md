# Everything Hub — Self-Owned Git & Publishing Platform

This is the first-party replacement layer for hosted Git and hosted static publishing. It is designed to run on infrastructure controlled by Everything Hub, not on AppDeploy, Cloudflare, or GitHub at runtime.

## What we are building

- **Git service:** Forgejo (Gitea-compatible, open-source) hosts repositories, branches, issues, releases, and Git-over-SSH/HTTPS.
- **Publishing gateway:** Caddy terminates HTTPS, routes Git traffic, and serves the public static site from an immutable release directory.
- **Atomic publisher:** a deployment script validates a release directory and switches the live site by changing one symlink. Rollback is a symlink switch, not a rebuild.
- **Build automation:** a self-hosted runner can be added after the origin host is available. Builds and publishing remain separate from the search crawler.
- **Edge strategy:** the gateway sets browser/CDN cache headers for versioned assets and short cache headers for HTML. A single origin is not a global CDN; regional replicas and cache nodes are a later scale phase.

## Safety and operating rules

- No paid infrastructure is created by this repository change.
- No domain DNS is changed.
- No production secrets are committed. Set all passwords and hostnames in the server's environment file.
- Do not expose the Forgejo admin interface to the public internet without HTTPS, strong credentials, registration disabled, and backups.
- Keep the public search catalog read-only at the origin. The publisher must not mutate crawler frontier/index data.
- Never point this stack at Dog Tag Day or Story Wall data.
- Do not claim this platform is live until it is installed on an owned host and externally health-checked.

## First deployment requirements

A host with Docker Compose, a persistent data volume, and inbound TCP 80/443 plus SSH is required. The current connected DigitalOcean account reports no droplets and a team limit warning, so this repository does not provision a server. No free compute host has been verified for persistent Forgejo hosting. The stack is therefore prepared but not yet online.

## Start

1. Copy `.env.example` to `.env` on the host and set unique secrets and DNS names.
2. Review the ports and firewall rules.
3. Run `docker compose -f platform/compose.yml up -d`.
4. Complete the Forgejo first-run setup privately; disable open registration and create the owner account.
5. Configure the first repository mirror/import from the current Everything Hub repository.
6. Publish the static site into `/srv/everythinghub/releases/<release-id>`, then run the atomic activation command below.

## Atomic static release

The web root is `/srv/everythinghub/current`. Each release is immutable and stored under `/srv/everythinghub/releases`. Build outside the live directory, validate the output, then atomically replace the symlink:

```sh
sudo install -d -m 0755 /srv/everythinghub/releases
sudo test -f "$RELEASE_DIR/index.html"
sudo ln -sfn "$RELEASE_DIR" /srv/everythinghub/current.next
sudo mv -Tf /srv/everythinghub/current.next /srv/everythinghub/current
```

Rollback uses the same operation with the previous release directory. Retain at least the last three known-good releases.

## Service boundaries

- Forgejo stores source and Git history.
- The build runner produces static output.
- The publishing gateway serves only the activated release.
- The crawler owns its own frontier and catalog generation.
- The search API consumes validated catalog releases; it does not write back to the Git service.
