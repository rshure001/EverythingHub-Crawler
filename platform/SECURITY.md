# Security checklist for the self-owned platform

- [ ] Use a supported OS with automatic security updates.
- [ ] Restrict SSH to key authentication; disable password login and root login.
- [ ] Firewall inbound traffic to 80/443 and the selected Git SSH port only.
- [ ] Keep Forgejo registration disabled; create the owner account during private first-run setup.
- [ ] Use unique secrets; keep populated `.env` outside Git.
- [ ] Back up Forgejo data and release directories off-host; test a restore.
- [ ] Keep the publishing origin read-only to the gateway container.
- [ ] Do not mount crawler frontier, database credentials, or Dog Tag Day data into Forgejo or the gateway.
- [ ] Add rate limiting, abuse controls, monitoring, and a tested incident rollback before public launch.
- [ ] Do not expose an admin panel on the public web hostname.
