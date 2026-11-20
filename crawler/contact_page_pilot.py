#!/usr/bin/env python3
"""Bounded, robots-aware, staging-only pilot for five public veteran contact pages."""
from __future__ import annotations

import argparse
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from html.parser import HTMLParser
from pathlib import Path

from safe_http import build_pinned_opener

ROOT = Path(__file__).resolve().parents[1]
USER_AGENT = "EverythingHubContactPilot/1.0 (+https://github.com/rshure001/EverythingHub-Crawler)"
MAX_BYTES = 1_000_000
MAX_TEXT = 1600


class SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects to a different origin, keeping the pilot host-bounded."""
    def __init__(self, origin, is_allowed=None):
        super().__init__()
        self.origin = origin
        self.is_allowed = is_allowed or (lambda _url: True)

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target_url = urllib.parse.urljoin(req.full_url, newurl)
        target = urllib.parse.urlsplit(target_url)
        target_origin = (target.scheme.lower(), (target.hostname or "").lower(),
                         target.port or (443 if target.scheme.lower() == "https" else 80))
        if target_origin != self.origin:
            raise urllib.error.HTTPError(req.full_url, code,
                                         "cross-origin redirect blocked", headers, fp)
        if not self.is_allowed(target_url):
            raise urllib.error.HTTPError(req.full_url, code,
                                         "redirect target blocked by robots.txt", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class PageTextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts = []
        self.description = ""
        self.text_parts = []
        self.in_title = False
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag.lower() == "title":
            self.in_title = True
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self.skip_depth += 1
        if tag.lower() == "meta" and attrs.get("name", "").lower() == "description":
            self.description = attrs.get("content", "")[:500]

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self.in_title = False
        if tag.lower() in {"script", "style", "noscript", "svg"} and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data):
        value = re.sub(r"\s+", " ", data).strip()
        if not value or self.skip_depth:
            return
        if self.in_title:
            self.title_parts.append(value)
        else:
            self.text_parts.append(value)


def validate_target(target, allowed_hosts):
    raw = target.get("url", "")
    parsed = urllib.parse.urlsplit(raw)
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("invalid target port") from exc
    host = (parsed.hostname or "").lower().rstrip(".")
    if (parsed.scheme.lower() != "https" or not host or host not in allowed_hosts
            or parsed.username or parsed.password or parsed.fragment):
        raise ValueError("target must be HTTPS, credential-free, fragment-free, and on an allowlisted host")
    if parsed.query:
        raise ValueError("query-string targets are not allowed in this pilot")
    return parsed, (parsed.scheme.lower(), host, parsed.port or 443)


def fetch_page(target, allowed_hosts, timeout=15):
    parsed, origin = validate_target(target, allowed_hosts)
    opener = build_pinned_opener(SameOriginRedirectHandler(origin))
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    request = urllib.request.Request(robots_url, headers={"User-Agent": USER_AGENT})
    try:
        with opener.open(request, timeout=timeout) as response:
            robots_text = response.read(256_001).decode("utf-8", errors="replace")
            if len(robots_text) > 256_000:
                raise RuntimeError("robots.txt exceeded the pilot size limit")
            if response.status != 200:
                raise RuntimeError("robots.txt response was not successful")
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 410):
            robots_text = ""
        else:
            return {"status": "robots_unavailable_fail_closed", "http_status": exc.code}
    except Exception as exc:
        return {"status": "robots_unavailable_fail_closed", "error": type(exc).__name__}

    robots = urllib.robotparser.RobotFileParser()
    robots.set_url(robots_url)
    robots.parse(robots_text.splitlines())
    if not robots.can_fetch(USER_AGENT, target["url"]):
        return {"status": "robots_disallowed"}

    # Redirect destinations must also pass the same robots policy.
    page_opener = build_pinned_opener(
        SameOriginRedirectHandler(
            origin,
            is_allowed=lambda url: robots.can_fetch(USER_AGENT, url),
        )
    )
    page_request = urllib.request.Request(
        target["url"],
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
    )
    try:
        with page_opener.open(page_request, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "").lower()
            if response.status != 200:
                return {"status": "http_error", "http_status": response.status}
            if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                return {"status": "not_html", "content_type": content_type[:120]}
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                return {"status": "page_too_large"}
            parser = PageTextParser()
            parser.feed(body.decode("utf-8", errors="replace"))
            title = re.sub(r"\s+", " ", " ".join(parser.title_parts)).strip()[:240]
            description = re.sub(r"\s+", " ", parser.description).strip()[:500]
            text = re.sub(r"\s+", " ", " ".join(parser.text_parts)).strip()[:MAX_TEXT]
            return {
                "status": "fetched",
                "http_status": response.status,
                "content_type": content_type[:120],
                "title": title,
                "description": description,
                "text_preview": text,
                "bytes_read": len(body),
            }
    except urllib.error.HTTPError as exc:
        return {"status": "http_error", "http_status": exc.code}
    except Exception as exc:
        return {"status": "fetch_error", "error": type(exc).__name__}


def run(config_path, output_path, fetcher=fetch_page):
    config = json.loads(config_path.read_text(encoding="utf-8"))
    allowed_hosts = set(config.get("allowed_hosts", []))
    targets = config.get("targets", [])
    limit = min(int(config.get("max_pages", 5)), 5)
    if not allowed_hosts or not isinstance(targets, list) or not 1 <= limit <= 5:
        raise ValueError("pilot configuration must define allowlisted hosts and a 1-5 page limit")
    if len(targets) > 5:
        raise ValueError("pilot is capped at five explicit targets")
    results = []
    seen = set()
    for target in targets[:limit]:
        parsed, _origin = validate_target(target, allowed_hosts)
        canonical = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        if canonical in seen:
            continue
        seen.add(canonical)
        result = fetcher(target, allowed_hosts)
        results.append({
            "pilot_id": config.get("pilot_id"),
            "organization": target.get("organization", ""),
            "source_type": target.get("source_type", ""),
            "url": canonical,
            **result,
            "production_published": False,
        })
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in results),
        encoding="utf-8",
    )
    summary = {
        "pilot_id": config.get("pilot_id"),
        "targets": len(results),
        "fetched": sum(row["status"] == "fetched" for row in results),
        "blocked_or_failed": sum(row["status"] != "fetched" for row in results),
        "production_published": 0,
        "output": str(output_path),
    }
    print(json.dumps(summary, indent=2))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "crawler/contact_pilot_sources.json")
    parser.add_argument("--output", type=Path, default=ROOT / "staging/contact-pilot/results.jsonl")
    args = parser.parse_args()
    run(args.config, args.output)


if __name__ == "__main__":
    main()
