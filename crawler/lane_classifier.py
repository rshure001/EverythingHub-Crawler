"""Deterministic search-lane classification for Everything Hub source hosts."""
from __future__ import annotations

from urllib.parse import urlsplit

VALID_LANES = frozenset({
    "general", "science", "news", "technology",
    "business", "government", "culture", "nature",
})

# Curated source-host routing. Exact domain boundaries prevent lookalike hosts
# such as nasa.gov.example.com from inheriting a trusted source's lane.
HOST_LANES = {
    "nasa.gov": "science",
    "noaa.gov": "science",
    "usgs.gov": "science",
    "nist.gov": "science",
    "nih.gov": "science",
    "cdc.gov": "science",
    "fda.gov": "science",
    "medlineplus.gov": "science",
    "weather.gov": "science",
    "who.int": "science",
    "redcross.org": "science",
    "bbc.com": "news",
    "reuters.com": "news",
    "apnews.com": "news",
    "npr.org": "news",
    "theguardian.com": "news",
    "pbs.org": "news",
    "cnbc.com": "business",
    "marketwatch.com": "business",
    "investopedia.com": "business",
    "sba.gov": "business",
    "usa.gov": "government",
    "congress.gov": "government",
    "supremecourt.gov": "government",
    "energy.gov": "government",
    "epa.gov": "government",
    "transportation.gov": "government",
    "ed.gov": "government",
    "dol.gov": "government",
    "census.gov": "government",
    "irs.gov": "government",
    "va.gov": "government",
    "un.org": "government",
    "unesco.org": "government",
    "nps.gov": "nature",
    "blm.gov": "nature",
    "nationalgeographic.com": "nature",
    "khanacademy.org": "technology",
    "mit.edu": "technology",
    "coursera.org": "technology",
    "edx.org": "technology",
    "openstreetmap.org": "culture",
    "wikivoyage.org": "culture",
    "nationalrail.co.uk": "culture",
    "loc.gov": "culture",
    "si.edu": "culture",
    "smithsonianmag.com": "culture",
}


def classify_lane(url: str) -> str:
    """Return one of the eight supported lanes; unknown hosts remain general."""
    try:
        host = (urlsplit(str(url)).hostname or "").lower().rstrip(".")
    except ValueError:
        return "general"
    if not host:
        return "general"
    for domain, lane in HOST_LANES.items():
        if host == domain or host.endswith("." + domain):
            return lane
    return "general"
