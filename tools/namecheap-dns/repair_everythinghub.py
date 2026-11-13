#!/usr/bin/env python3
"""Safely add/repair the EverythingHub Namecheap CNAME without touching apex DNS.

Required environment:
  NAMECHEAP_API_USER, NAMECHEAP_API_KEY, NAMECHEAP_USERNAME,
  NAMECHEAP_CLIENT_IP (the IP authorized in Namecheap)

Dry-run is the default. Pass --apply to write.
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

API = "https://api.namecheap.com/xml.response"
DOMAIN = "dogtagday.org"
HOST = "everything"
TARGET = "0032978e20fb602e.vercel-dns-017.com"
ROOT_A = {"185.199.108.153", "185.199.109.153", "185.199.110.153", "185.199.111.153"}


def api_call(command, **extra):
    params = {
        "ApiUser": os.environ["NAMECHEAP_API_USER"],
        "ApiKey": os.environ["NAMECHEAP_API_KEY"],
        "UserName": os.environ["NAMECHEAP_USERNAME"],
        "ClientIp": os.environ["NAMECHEAP_CLIENT_IP"],
        "Command": command,
        "SLD": "dogtagday",
        "TLD": "org",
        **extra,
    }
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "EverythingHub-DNS-SafeUpdater/1.0"})
    with urllib.request.urlopen(req, timeout=30) as response:
        body = response.read()
    root = ET.fromstring(body)
    errors = [e.text or "" for e in root.findall(".//{*}Error")]
    if errors:
        raise RuntimeError("; ".join(errors))
    return root


def get_hosts():
    root = api_call("namecheap.domains.dns.getHosts")
    hosts = []
    for node in root.findall(".//{*}host"):
        hosts.append({
            "HostName": node.attrib.get("Name", ""),
            "RecordType": node.attrib.get("Type", ""),
            "Address": node.attrib.get("Address", ""),
            "MXPref": node.attrib.get("MXPref", "10"),
            "TTL": node.attrib.get("TTL", "1800"),
        })
    if not hosts:
        raise RuntimeError("Namecheap returned no host records; refusing to update an empty zone.")
    return hosts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Actually submit the complete preserved host-record set.")
    args = parser.parse_args()

    required = ["NAMECHEAP_API_USER", "NAMECHEAP_API_KEY", "NAMECHEAP_USERNAME", "NAMECHEAP_CLIENT_IP"]
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        sys.exit("Missing environment variables: " + ", ".join(missing))

    hosts = get_hosts()
    apex_a = {h["Address"] for h in hosts if h["HostName"] in ("@", "") and h["RecordType"] == "A"}
    if not ROOT_A.issubset(apex_a):
        sys.exit("Safety stop: expected all four existing GitHub Pages apex A records; no changes made.")

    before = [dict(h) for h in hosts]
    matches = [i for i, h in enumerate(hosts) if h["HostName"] == HOST]
    cname = {"HostName": HOST, "RecordType": "CNAME", "Address": TARGET, "MXPref": "10", "TTL": "1800"}
    if len(matches) == 1:
        hosts[matches[0]] = cname
    elif len(matches) == 0:
        hosts.append(cname)
    else:
        sys.exit("Safety stop: multiple 'everything' records found. Manual review required.")

    after_apex = {h["Address"] for h in hosts if h["HostName"] in ("@", "") and h["RecordType"] == "A"}
    if not ROOT_A.issubset(after_apex):
        sys.exit("Safety stop: apex A records would change.")

    backup = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "domain": DOMAIN,
        "records_before": before,
        "records_to_submit": hosts,
        "apex_a_records_preserved": sorted(ROOT_A),
    }
    backup_path = "namecheap-dns-backup-dogtagday-org.json"
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(backup, f, indent=2)
    print(json.dumps({"mode": "APPLY" if args.apply else "DRY RUN", "host": HOST, "type": "CNAME", "target": TARGET, "record_count": len(hosts), "backup": backup_path, "apex_records_preserved": sorted(ROOT_A)}, indent=2))

    if not args.apply:
        print("No DNS changes made. Review the backup, then rerun with --apply.")
        return

    params = {}
    for i, h in enumerate(hosts, 1):
        for key in ("HostName", "RecordType", "Address", "MXPref", "TTL"):
            params[f"{key}{i}"] = h[key]
    result = api_call("namecheap.domains.dns.setHosts", **params)
    status = result.find(".//{*}DomainDNSSetHostsResult")
    if status is None or status.attrib.get("IsSuccess", "").lower() != "true":
        raise RuntimeError("Namecheap did not confirm the DNS update.")
    print("Namecheap confirmed DNS update. Verify DNS propagation and Vercel domain status.")


if __name__ == "__main__":
    main()
