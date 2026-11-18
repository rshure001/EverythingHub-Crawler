#!/usr/bin/env python3
"""Polite, resumable, depth-limited crawler for Everything Hub."""
import concurrent.futures
import email.utils
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from collections import deque
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

from lane_classifier import VALID_LANES, classify_lane
from safe_http import UnsafeTargetError, build_pinned_opener
from media_discovery import discover_media, merge_media_candidates

ROOT = Path(__file__).resolve().parents[1]
SEEDS = ROOT / "crawler" / "seeds.txt"
OUTPUT = ROOT / "data" / "index.json"
FRONTIER = ROOT / "data" / "frontier.json"
MEDIA_DISCOVERY = ROOT / "data" / "media-discovery.json"
WORKERS = 16
QUEUE_LIMIT = 50000
MAX_DEPTH = 3
MAX_FETCH_PER_RUN = 1600
MAX_SITEMAP_HOSTS_PER_RUN = 3
MAX_SITEMAP_CHILD_SITEMAPS = 2
MAX_SITEMAP_URLS_PER_HOST = 100
MAX_SITEMAP_BYTES = 1_000_000
SITEMAP_RECHECK_SECONDS = 7 * 24 * 60 * 60
MAX_RETRY_AFTER_SECONDS = 7 * 24 * 60 * 60
DEFAULT_429_BACKOFF_SECONDS = 3600
DEFAULT_5XX_BACKOFF_SECONDS = 600
DEFAULT_NETWORK_BACKOFF_SECONDS = 60
MAX_RUN_SECONDS = 360
HOST_MIN_INTERVAL = 1.0
ROBOTS_MAX_BYTES = 512_000
TIMEOUT = 15
UA = "EverythingHubCrawler/1.1 (+https://github.com/rshure001/EverythingHub-Crawler)"
ACCESS_CHALLENGE_MAX_TEXT = 1200
PUBLISHED_DATE_META_PRIORITY = {
    "article:published_time": 0,
    "og:published_time": 0,
    "datepublished": 0,
    "dc.date.issued": 1,
    "dcterms.issued": 1,
    "parsely-pub-date": 1,
    "sailthru.date": 1,
    "pubdate": 2,
    "publishdate": 2,
    "publish_date": 2,
    "pub_date": 2,
    "date": 3,
}

