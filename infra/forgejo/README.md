# Everything Hub — self-hosted Git service

This stack runs Forgejo and PostgreSQL on infrastructure controlled by Everything Hub. It uses the official Forgejo rootless container, persistent Docker volumes, a private database network, and no public database port.

## Current deployment status

This is a prepared deployment stack, not a running server. No cloud resource is created by these files. Provisioning must wait until the DigitalOcean account's droplet-limit warning is cleared and the owner approves any billable resource.

## Components

- Forgejo 17 rootless container for Git repositories, issues, pull requests, packages, and Actions.
- PostgreSQL 17 Alpine for durable application data.
- Docker-managed persistent volumes for Forgejo and PostgreSQL.
- Web UI bound to `127.0.0.1:3000` so it is not exposed directly to the public internet.
- Git SSH on TCP `2222`; restrict it with the host firewall.
- Registration disabled by default; repositories default to private.

## Host requirements

Use a supported 64-bit Linux host with Docker Engine and the Docker Compose plugin. For a small initial deployment, plan for at least 2 vCPU, 2 GB RAM, and 40 GB SSD, then monitor actual memory, disk, and repository growth. Keep the host's administrative SSH on port 22; Forgejo Git SSH uses 2222.

## Configure and start

From the repository root on the server:

```sh
cd infra/forgejo
cp .env.example .env
```

Edit `.env` locally. Set:
- `FORGEJO_DOMAIN` to the Git hostname.
- `FORGEJO_ROOT_URL` to the final HTTPS URL, including the trailing slash.
- `FORGEJO_DB_PASSWORD` to a long, unique random secret.

Do not commit `.env`, paste the database password into chat, or put it in a compose file.

Start the services:

```sh
docker compose config
docker compose up -d
docker compose ps
docker compose logs --tail=100 server db
```

The web interface listens only on the host loopback address at port 3000. Put a TLS reverse proxy or Cloudflare Tunnel in front of that local listener; do not open port 3000 in the public firewall. Configure the proxy's public hostname to forward to `http://127.0.0.1:3000`.

Open TCP 2222 in the host firewall only if Git-over-SSH access is required. Keep TCP 5432 private; this stack does not publish it to the host.

## First-run checklist

1. Confirm HTTPS works and the configured root URL matches the public hostname.
2. Complete the initial administrator setup through the protected HTTPS endpoint.
3. Keep public registration disabled.
4. Add owner SSH keys and verify clone/push over port 2222.
5. Create a private test repository and verify clone, push, pull request, and backup/restore.
6. Configure a separate Forgejo Actions runner only after its isolation and permissions are reviewed. Do not run untrusted workflows on the Git host itself.

## Operations and recovery

- Check service health with `docker compose ps` and logs with `docker compose logs --tail=200`.
- Back up both the Forgejo data volume and PostgreSQL database together. A database dump alone is not a complete backup of Git repositories, LFS objects, attachments, and configuration.
- Store encrypted backups off-host and test a restore before relying on them.
- Upgrade within the pinned major version after reviewing release notes and taking a tested backup. Major-version upgrades require a planned migration and human verification; do not blindly change the image tag.
- Monitor disk space, memory, PostgreSQL health, and backup age.

## Security notes

- The database has no host-published port.
- The Forgejo web listener is loopback-only and must sit behind HTTPS.
- Registration is disabled and new repositories default to private.
- Secrets live in the untracked local `.env` file.
- Keep the host OS, Docker, Forgejo, and PostgreSQL patched.
- Treat CI runners as a separate trust boundary. A runner that executes repository workflows can access whatever the runner host exposes.

## Official references

- Forgejo Docker installation: https://forgejo.org/docs/latest/admin/installation/docker/
- Forgejo Actions runner installation: https://forgejo.org/docs/latest/admin/actions/installation/
