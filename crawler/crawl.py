#!/usr/bin/env python3
"""Polite, bounded, depth-limited crawler for Everything Hub."""
import concurrent.futures, json, re, time, urllib.parse, urllib.request, urllib.robotparser, xml.etree.ElementTree as ET
from collections import deque
from html.parser import HTMLParser
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SEEDS=ROOT/"crawler"/"seeds.txt"
OUTPUT=ROOT/"data"/"index.json"
WORKERS=8
QUEUE_LIMIT=500
MAX_DEPTH=3
MAX_FETCH_PER_RUN=80
TIMEOUT=15
UA="EverythingHubCrawler/1.0 (+https://github.com/rshure001/EverythingHub-Crawler)"

class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.title_on=False; self.title=[]; self.description=""; self.text=[]; self.links=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag.lower()=="title": self.title_on=True
        if tag.lower()=="meta" and a.get("name","").lower()=="description": self.description=a.get("content","")
        if tag.lower()=="a" and a.get("href"): self.links.append(a["href"])
    def handle_endtag(self,tag):
        if tag.lower()=="title": self.title_on=False
    def handle_data(self,data):
        if self.title_on: self.title.append(data)
        if data.strip(): self.text.append(data.strip())

def request(url):
    req=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"text/html,application/xml,*/*"})
    with urllib.request.urlopen(req,timeout=TIMEOUT) as r:
        return r.geturl(),r.headers.get_content_type(),r.read(2_000_000)

def clean(url):
    p=urllib.parse.urlsplit(url)
    if p.scheme not in ("http","https") or not p.netloc: return None
    return urllib.parse.urlunsplit((p.scheme,p.netloc,p.path or "/", "", ""))

def main():
    seeds=[clean(x.strip()) for x in SEEDS.read_text().splitlines() if x.strip() and not x.startswith("#")]
    seeds=list(dict.fromkeys(x for x in seeds if x))
    robots={}
    def allowed(url):
        p=urllib.parse.urlsplit(url); origin=f"{p.scheme}://{p.netloc}"
        if origin not in robots:
            rp=urllib.robotparser.RobotFileParser(); rp.set_url(origin+"/robots.txt")
            try: rp.read()
            except Exception: pass
            robots[origin]=rp
        return robots[origin].can_fetch(UA,url)
    def sitemap_candidates(home):
        origin=f"{urllib.parse.urlsplit(home).scheme}://{urllib.parse.urlsplit(home).netloc}"
        urls=[origin+"/sitemap.xml"]
        try:
            final,kind,raw=request(origin+"/robots.txt")
            for line in raw.decode("utf-8","ignore").splitlines():
                if line.lower().startswith("sitemap:"): urls.insert(0,line.split(":",1)[1].strip())
        except Exception: pass
        return list(dict.fromkeys(urls))
    queue=deque((u,0) for u in seeds)
    seen=set(seeds); records={}
    # Sitemap discovery is performed for each approved seed, without leaving its host.
    for home in seeds:
        for sm in sitemap_candidates(home):
            if not allowed(sm): continue
            try:
                _,_,raw=request(sm); root=ET.fromstring(raw)
                ns={"s":"http://www.sitemaps.org/schemas/sitemap/0.9"}
                for node in root.findall(".//s:loc",ns):
                    if not node.text: continue
                    u=clean(node.text.strip())
                    if u and urllib.parse.urlsplit(u).netloc==urllib.parse.urlsplit(home).netloc and u not in seen and len(seen)<QUEUE_LIMIT:
                        seen.add(u); queue.append((u,1))
            except Exception: pass
    fetched=0
    while queue and fetched<MAX_FETCH_PER_RUN:
        batch=[]
        while queue and len(batch)<WORKERS and fetched+len(batch)<MAX_FETCH_PER_RUN:
            url,depth=queue.popleft()
            if depth<=MAX_DEPTH and allowed(url): batch.append((url,depth))
        if not batch: continue
        def fetch(item):
            url,depth=item
            try:
                final,kind,raw=request(url)
                if kind!="text/html": return None
                parser=PageParser(); parser.feed(raw.decode("utf-8","replace"))
                title=re.sub(r"\s+"," "," ".join(parser.title)).strip()
                body=re.sub(r"\s+"," "," ".join(parser.text)).strip()[:1500]
                links=[]
                if depth<MAX_DEPTH:
                    for href in parser.links:
                        u=clean(urllib.parse.urljoin(final,href))
                        if u and urllib.parse.urlsplit(u).netloc==urllib.parse.urlsplit(url).netloc: links.append(u)
                return ({"url":final,"title":title,"description":parser.description[:500],"text":body,"depth":depth,"fetched_at":int(time.time())},links)
            except Exception as e:
                print(f"skip {url}: {e}"); return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
            results=list(pool.map(fetch,batch))
        fetched+=len(batch)
        for result in results:
            if not result: continue
            record,links=result; records[record["url"]]=record
            for u in links:
                if u not in seen and len(seen)<QUEUE_LIMIT:
                    seen.add(u); queue.append((u,record["depth"]+1))
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    try: old=json.loads(OUTPUT.read_text())
    except Exception: old={"version":1,"pages":[]}
    merged={p["url"]:p for p in old.get("pages",[]) if p.get("url")}
    merged.update(records)
    OUTPUT.write_text(json.dumps({"version":1,"updated_at":int(time.time()),"pages_crawled_this_run":fetched,"queue_limit":QUEUE_LIMIT,"max_depth":MAX_DEPTH,"pages":list(merged.values())},ensure_ascii=False,indent=2)+"\n")
    print(f"Fetched {fetched} pages; {len(records)} new/updated this run; {len(merged)} total indexed; {len(seen)} URLs discovered.")
if __name__=="__main__": main()
