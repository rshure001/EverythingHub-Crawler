# EverythingHub DNS repair (Namecheap)

This utility manages only the `everything.dogtagday.org` CNAME. It uses Namecheap's official API directly; it does not use a browser automation service.

## Safety behavior
- Dry-run by default; no write occurs without `--apply`.
- Reads the current Namecheap DNS host records first.
- Stops unless the four existing DogTagDay apex GitHub Pages A records are present.
- Preserves the complete current host-record set and changes/adds only the single `everything` host record.
- Stops if more than one `everything` host record exists.
- Saves a JSON backup before any API write.
- Does not change nameservers, root-domain A records, or other subdomains.

Target record:
- Type: CNAME
- Host: `everything`
- Value: `0032978e20fb602e.vercel-dns-017.com`

## Requirements
1. Enable Namecheap API access for the account.
2. Authorize the public IP of the machine running this script in Namecheap's API access settings.
3. Provide credentials as environment variables (never commit them):
   - `NAMECHEAP_API_USER`
   - `NAMECHEAP_API_KEY`
   - `NAMECHEAP_USERNAME`
   - `NAMECHEAP_CLIENT_IP`

## Run
```bash
python3 tools/namecheap-dns/repair_everythinghub.py
# Inspect the generated backup and dry-run summary.
python3 tools/namecheap-dns/repair_everythinghub.py --apply
```

After Namecheap confirms the update, verify the CNAME with DNS lookup and verify the custom domain in the existing Vercel project.
