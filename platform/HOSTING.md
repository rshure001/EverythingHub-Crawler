# Everything Hub Hosting

This layer is our hosting product: the operating procedures and services that run our own Git, web origin, and release pipeline. It is provider-independent and can run on a physical machine we control or on a compatible virtual machine. It does not require Oracle, Cloudflare, Vercel, or AppDeploy at runtime.

## Hosting components

- **Origin gateway:** Caddy handles HTTPS and routes the Git and website hostnames.
- **Git hosting:** Forgejo stores repositories and Git history.
- **Static origin:** immutable site releases are served read-only by the gateway.
- **Release manager:** `activate-release.sh` performs atomic activation and rollback.
- **Host preflight:** `host-preflight.sh` checks Docker, Compose, RAM, disk, DNS resolution, and Compose validity without changing the host.
- **Backup utility:** `backup-host.sh` creates permission-restricted local backups of Forgejo data and published releases, plus checksums.

## Host requirements

For the first single-node deployment, use a supported Linux host with at least 2 GiB RAM (4 GiB recommended), 10 GiB free disk (more for repositories and release history), Docker Engine with Compose, persistent storage, stable power, and public inbound TCP 80/443. Git SSH uses TCP 2222. A static public IP or reliable dynamic DNS is needed. A home-hosted system also needs router port forwarding and an ISP plan that permits hosting; carrier-grade NAT may prevent inbound access.

## Safe bring-up

1. Prepare the host and secure SSH access; do not expose password-based root login.
2. Copy `platform/.env.example` to the repository root as `.env` and configure private values.
3. Run `bash platform/host-preflight.sh`. This is read-only and must pass before installation.
4. Review firewall and router rules manually; the scripts do not alter them.
5. Start services with Docker Compose and verify HTTPS from an external network.
6. Import/mirror source repositories and publish a known-good static release.
7. Run a backup, copy it to a separate encrypted location, and test restoration.

## Boundaries

This is our software, not our physical datacenter. A provider-independent stack still needs a machine, electricity, connectivity, public reachability, and backups. We currently have no confirmed always-on free host. Do not claim production hosting is online until external health checks pass. No DNS is changed by these scripts. They do not touch crawler databases, Dog Tag Day data, or Story Wall data.
