# Everything Hub Cloud

Everything Hub Cloud is the planned private compute platform behind Everything Hub. It will provide a control panel and API for creating, starting, stopping, inspecting, and deleting isolated virtual machines ("droplets") on infrastructure we control.

## Design decision

Start with a **single-node private cloud**, then expand to multiple nodes only after the first node is stable.

- **Hypervisor:** Proxmox VE / KVM (open-source virtualization stack).
- **VM images:** cloud-init-enabled Ubuntu LTS templates.
- **Control plane:** Everything Hub-owned API and dashboard; it talks to the hypervisor API over a private management network.
- **Networking:** private VM bridge by default; public ingress only through an authenticated reverse proxy/firewall.
- **Storage:** local mirrored storage for the first node; tested off-host backups before production data.
- **Identity and secrets:** separate operator credentials, short-lived API tokens where supported, secrets kept outside Git.
- **Observability:** node health, CPU, RAM, disk, VM state, backup status, and audit log.

This is our management software and operating model. Proxmox/KVM supplies the low-level virtualization engine; it is not a requirement to rent DigitalOcean droplets.

## Phased build

1. **Hardware qualification** — confirm a machine has hardware virtualization, sufficient RAM and SSD/NVMe, reliable cooling, and a stable wired network. The included `host-preflight.sh` is read-only and gathers local suitability facts.
2. **Lab node** — install the hypervisor on a dedicated machine only after its disks and existing data are understood and backed up. Hypervisor installation can erase or repartition disks; never automate that step.
3. **Golden VM** — create one cloud-init template, test console and SSH access, updates, firewalling, and snapshot/restore.
4. **Cloud API** — implement authenticated inventory and lifecycle endpoints with allow-listed image/size profiles, quotas, audit records, and idempotent actions.
5. **Dashboard** — Everything Hub-branded VM list, create flow, health view, and operator controls.
6. **Production hardening** — private management access, TLS, backups, restore drills, monitoring, rate limits, and documented incident recovery.
7. **Multi-node expansion** — add a second qualified host and shared/replicated storage only when capacity and availability justify it.

## Safety boundaries

- No paid server, domain, backup, or network service is provisioned without explicit approval.
- No host is installed or reimaged until its hardware, disks, and existing data are identified and the operator approves the destructive installation.
- Never expose the hypervisor management interface or PostgreSQL port directly to the public internet.
- VM creation is restricted to approved images, CPU/RAM/disk ceilings, and private networking by default.
- Destructive VM/disk actions require explicit confirmation and are recorded in an audit log.
- Backups are not considered valid until a restore has been tested.
- The managed Supabase database, current production search, and crawler index remain unchanged during the lab phase.

## Current state

- Architecture defined in this repository.
- No physical host has been qualified yet.
- No hypervisor has been installed.
- No VMs/droplets have been created.
- No paid infrastructure has been ordered.
- The existing self-hosted PostgreSQL stack remains a separate deployment target; it can move onto a VM only after the VM and database pass health, backup, and restore checks.

## First milestone

Identify one dedicated host that can safely run the first node. Minimum practical starting point: x86-64 CPU with Intel VT-x or AMD-V, 4 CPU cores, 16 GB RAM (32 GB preferred), SSD storage (500 GB preferred), wired Ethernet, and a stable always-on internet connection. More RAM and mirrored SSD/NVMe storage are strongly preferred for database and crawler workloads.

Run the read-only preflight script on a candidate Linux machine:

```bash
bash cloud/host-preflight.sh
```

The script does not install software, change settings, read user files, or contact external services.
