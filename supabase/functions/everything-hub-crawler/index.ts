import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "npm:@supabase/supabase-js@2.49.1";

const db = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!, { auth: { persistSession: false } });
const UA = "EverythingHubBot/1.1 (+https://everythinghub-search-free-rshure001-8053.vercel.app/)";
const LANES = ["general","science","news","technology","business","government","culture","nature"];
const now = () => new Date().toISOString();

function private4(ip:string) {
  const p=ip.split(".").map(Number);
  if(p.length!==4 || p.some(n=>!Number.isInteger(n)||n<0||n>255)) return true;
  return p[0]===0||p[0]===10||p[0]===127||p[0]>=224||(p[0]===169&&p[1]===254)||(p[0]===172&&p[1]>=16&&p[1]<=31)||(p[0]===192&&p[1]===168)||(p[0]===100&&p[1]>=64&&p[1]<=127);
}
function private6(ip:string) { const s=ip.toLowerCase(); return s==="::"||s==="::1"||s.startsWith("fc")||s.startsWith("fd")||/^fe[89ab]/.test(s)||s.startsWith("::ffff:")||s.startsWith("2001:db8:"); }
let activeDnsLookups=0;
const dnsWaiters:Array<()=>void>=[];
const dnsCache = new Map<string, Promise<{a:string[],aaaa:string[]}>>();
async function withDnsSlot<T>(task:()=>Promise<T>):Promise<T>{
  if(activeDnsLookups>=50)await new Promise<void>(resolve=>dnsWaiters.push(resolve));
  activeDnsLookups++;
  try{return await task();}
  finally{activeDnsLookups--;dnsWaiters.shift()?.();}
}
function resolveHost(host:string):Promise<{a:string[],aaaa:string[]}> {
  const cached=dnsCache.get(host);
  if(cached)return cached;
  const pending=withDnsSlot(async()=>{
    const [a,aaaa]=await Promise.all([
      Deno.resolveDns(host,"A").catch(()=>[] as string[]),
      Deno.resolveDns(host,"AAAA").catch(()=>[] as string[])
    ]);
    return {a,aaaa};
  });
  dnsCache.set(host,pending);
  return pending;
}
async function safeUrl(raw:string,base?:string):Promise<URL|null>{
  try{
    const u=new URL(raw,base); if(!["http:","https:"].includes(u.protocol)||u.username||u.password||(u.port&&!["80","443"].includes(u.port)))return null;
    const h=u.hostname.toLowerCase().replace(/^\[|\]$/g,"");
    if(!h.includes(".")||h==="localhost"||h.endsWith(".localhost")||h.endsWith(".local"))return null;
    if(/^\d{1,3}(\.\d{1,3}){3}$/.test(h)&&private4(h))return null;
    if(h.includes(":")&&private6(h))return null;
    if(!/^\d{1,3}(\.\d{1,3}){3}$/.test(h)&&!h.includes(":")){
      const {a,aaaa}=await resolveHost(h);
      if(!a.length&&!aaaa.length)return null;
      if(a.some((x:string)=>private4(x))||aaaa.some((x:string)=>private6(x)))return null;
    }
    u.hash="";
    for(const k of [...u.searchParams.keys()])if(/^(utm_.+|fbclid|gclid|ref|source)$/i.test(k))u.searchParams.delete(k);
    if(/\.(pdf|zip|jpe?g|png|gif|webp|svg|mp4|mp3|docx?|xlsx?|pptx?|css|js|xml|json)$/i.test(u.pathname))return null;
    return u;
  }catch{return null;}
}
function clean(s:string){return s.replace(/<(script|style|noscript|svg|nav|footer|header|form)\b[^>]*>[\s\S]*?<\/\1>/gi," ").replace(/<!--([\s\S]*?)-->/g," ").replace(/<[^>]+>/g," ").replace(/&nbsp;/gi," ").replace(/&amp;/gi,"&").replace(/&quot;/gi,'"').replace(/&#39;/g,"'").replace(/&lt;/gi,"<").replace(/&gt;/gi,">").replace(/\s+/g," ").trim();}
function extract(html:string,pageUrl:string){
  const title=(clean(html.match(/<title[^>]*>([\s\S]*?)<\/title>/i)?.[1]||"")||new URL(pageUrl).hostname).slice(0,240);
  const metaTags=html.match(/<meta\b[^>]*>/gi)||[];
  const metaValue=(keys:string[])=>{
    for(const tag of metaTags){
      const attrs=Object.fromEntries(Array.from(tag.matchAll(/([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*["']([^"']*)["']/g)).map(m=>[m[1].toLowerCase(),m[2]]));
      const key=(attrs.name||attrs.property||"").toLowerCase();
      if(keys.includes(key)&&attrs.content)return clean(attrs.content);
    }
    return "";
  };
  const description=(metaValue(["description","og:description","twitter:description"])||"").slice(0,600);
  let content=clean(html);
  if(content.length<160){
    const headings=(html.match(/<h[1-4]\b[^>]*>[\s\S]*?<\/h[1-4]>/gi)||[]).map(x=>clean(x)).filter(Boolean).join(" ");
    content=[title,description,headings].filter(Boolean).join(" ").replace(/\s+/g," ").trim();
  }
  content=content.slice(0,14000);
  const links:string[]=[];
  for(const a of html.match(/<a\b[^>]*href=["'][^"']+["'][^>]*>/gi)||[]){
    const raw=a.match(/href=["']([^"']+)["']/i)?.[1]; if(!raw)continue;
    try{const absolute=new URL(raw,pageUrl).toString();if(!links.includes(absolute))links.push(absolute);}catch{}
    if(links.length>=30)break;
  }
  return {title,description,content,links};
}

function extractMedia(html:string,pageUrl:string,title:string,description:string){
  const out=new Map<string,any>();
  const add=(raw:string|undefined,type:"image"|"video",alt="",poster?:string)=>{
    if(!raw)return;try{
      const u=new URL(raw.replace(/&amp;/g,"&"),pageUrl);
      if(!["http:","https:"].includes(u.protocol)||u.username||u.password)return;
      if(/\.(svg|ico)$/i.test(u.pathname))return;
      const mediaUrl=u.toString();if(out.has(mediaUrl))return;
      const host=u.hostname.toLowerCase();
      const provider=/youtube\.com|youtu\.be/i.test(host)?"YouTube":/vimeo\.com/i.test(host)?"Vimeo":/dailymotion\.com/i.test(host)?"Dailymotion":/twitch\.tv/i.test(host)?"Twitch":host;
      out.set(mediaUrl,{page_url:pageUrl,media_url:mediaUrl,media_type:type,title:(alt||title||"").slice(0,300),description:(description||"").slice(0,600),host,source_host:new URL(pageUrl).hostname,media_provider:provider,thumbnail_url:poster||null,fetched_at:now()});
    }catch{}
  };
  for(const m of html.matchAll(/<meta\b[^>]*(?:property|name)\s*=\s*["'](?:og:image(?::secure_url)?|twitter:image(?::src)?|twitter:player|og:video(?::secure_url)?|twitter:player:stream)["'][^>]*>/gi)){
    const tag=m[0],key=tag.match(/(?:property|name)\s*=\s*["']([^"']+)/i)?.[1]?.toLowerCase()||"";
    const val=tag.match(/content\s*=\s*["']([^"']+)/i)?.[1];
    if(key.includes("video")||key==="twitter:player")add(val,"video",title);
    else add(val,"image",title);
  }
  for(const m of html.matchAll(/<img\b[^>]*>/gi)){
    const tag=m[0],alt=tag.match(/\balt\s*=\s*["']([^"']*)/i)?.[1]||"";
    const raw=tag.match(/\b(?:src|data-src|data-original|data-lazy-src)\s*=\s*["']([^"']+)/i)?.[1]||tag.match(/\bsrcset\s*=\s*["']([^"']+)/i)?.[1]?.split(",")[0]?.trim().split(/\s+/)[0];
    add(raw,"image",alt||title);
  }
  for(const m of html.matchAll(/<(?:video|source)\b[^>]*>/gi)){
    const tag=m[0],raw=tag.match(/\bsrc\s*=\s*["']([^"']+)/i)?.[1],poster=tag.match(/\bposter\s*=\s*["']([^"']+)/i)?.[1];
    add(raw,"video",title,poster);
  }
  for(const m of html.matchAll(/<iframe\b[^>]*>/gi)){
    const tag=m[0],raw=tag.match(/\bsrc\s*=\s*["']([^"']+)/i)?.[1];
    if(raw&&/(youtube\.com\/embed|youtu\.be\/|player\.vimeo\.com\/video|dailymotion\.com\/embed\/video|twitch\.tv\/.*player)/i.test(raw))add(raw,"video",title);
  }
  for(const m of html.matchAll(/["'](?:contentUrl|embedUrl|thumbnailUrl)["']\s*:\s*["']([^"']+)["']/gi)){
    const raw=m[1],key=m[0].toLowerCase();
    add(raw,key.includes("thumbnail")?"image":"video",title);
  }
  return [...out.values()].slice(0,80);
}

const robotsCache=new Map<string,Promise<boolean>>();
function robotsAllowed(url:URL):Promise<boolean>{
  if(!robotsCache.has(url.origin))robotsCache.set(url.origin,(async()=>{
    try{
      const r=await fetch(new URL("/robots.txt",url.origin),{headers:{"User-Agent":UA},redirect:"error",signal:AbortSignal.timeout(5000)});
      if(r.status===404)return true;if(!r.ok)return false;
      const lines=(await r.text()).split(/\r?\n/);let agents:string[]=[];let rules:{allow:boolean,path:string}[]=[];const groups:{agents:string[],rules:{allow:boolean,path:string}[]}[]=[];
      for(const raw of lines){const m=raw.split("#")[0].trim().match(/^([^:]+):\s*(.*)$/);if(!m)continue;const k=m[1].toLowerCase(),v=m[2].trim();
        if(k==="user-agent"){if(agents.length&&rules.length){groups.push({agents,rules});agents=[];rules=[];}agents.push(v.toLowerCase());}
        else if((k==="allow"||k==="disallow")&&v)rules.push({allow:k==="allow",path:v});
      }
      if(agents.length)groups.push({agents,rules});
      const specific=groups.filter(g=>g.agents.includes("everythinghubbot"));const selected=specific.length?specific:groups.filter(g=>g.agents.includes("*"));
      const applicable=selected.flatMap(g=>g.rules).filter(x=>(url.pathname+url.search).startsWith(x.path)).sort((a,b)=>b.path.length-a.path.length);
      return applicable.length?applicable[0].allow:true;
    }catch{return false;}
  })());
  return robotsCache.get(url.origin)!;
}

const MAX_HTML_BYTES = 500_000;
async function readHtmlLimited(response: Response, maxBytes = MAX_HTML_BYTES): Promise<string> {
  if (!response.body) return "";
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const result = await reader.read();
      if (result.done) break;
      const value = result.value;
      const remaining = maxBytes - total;
      if (remaining <= 0) { await reader.cancel("HTML byte limit reached"); break; }
      if (value.byteLength > remaining) {
        chunks.push(value.subarray(0, remaining));
        total += remaining;
        await reader.cancel("HTML byte limit reached");
        break;
      }
      chunks.push(value);
      total += value.byteLength;
      if (total >= maxBytes) { await reader.cancel("HTML byte limit reached"); break; }
    }
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return new TextDecoder("utf-8").decode(bytes);
}
async function updateQueue(id: string, patch: Record<string, unknown>, context: string) {
  const result = await db.from("eh_crawl_queue").update(patch).eq("id", id).select("id").maybeSingle();
  if (result.error) throw new Error("Queue update failed (" + context + "): " + result.error.message);
  if (!result.data) throw new Error("Queue update matched no row (" + context + "): " + id);
}

async function retryOrFail(item:any,message:string){
  const attempts=(item.attempts||0)+1;
  // Permanent responses are not retried; preserve capacity for crawlable pages.
  const permanent=/HTTP (?:400|401|403|404|406|410|415|422)|Non-HTML response|Insufficient readable page text|URL rejected|robots\.txt/i.test(message);
  const status=permanent||attempts>=3?"failed":"pending";
  const patch:any={status,attempts,locked_at:null,last_error:message.slice(0,300),updated_at:now()};
  if(status==="pending") patch.next_attempt_at=new Date(Date.now()+Math.min(attempts*15,60)*60000).toISOString();
  // Terminal rows omit next_attempt_at so the NOT NULL database default is preserved.
  await updateQueue(item.id,patch,"retry/fail");
}
async function process(item:any){
  try{
    const initialUrl = await safeUrl(item.url);
    if (initialUrl === null) { await updateQueue(item.id,{status:"blocked",locked_at:null,last_error:"URL rejected by public-network safety filter",updated_at:now()},"invalid URL"); return {blocked:1,fetched:0,added:0,discovered:0,errors:0}; }
    let url: URL = initialUrl;
    if(!await robotsAllowed(url)){await updateQueue(item.id,{status:"blocked",locked_at:null,last_error:"robots.txt disallows crawling or could not be read",updated_at:now()},"robots denied");return {blocked:1,fetched:0,added:0,discovered:0,errors:0};}
    const visited=new Set<string>();let response:Response|undefined;
    // Follow a short, safety-checked redirect chain instead of failing on a second hop.
    for(let hop=0;hop<6;hop++){
      if(visited.has(url.toString()))throw new Error("Redirect loop detected");
      visited.add(url.toString());
      response=await fetch(url,{headers:{"User-Agent":UA,"Accept":"text/html,application/xhtml+xml;q=0.9"},redirect:"manual",signal:AbortSignal.timeout(15000)});
      if(response.status<300||response.status>=400)break;
      const loc: string | null = response.headers.get("location");
      const nextUrl: URL | null = loc === null ? null : await safeUrl(loc,url.toString());
      if(nextUrl === null){await updateQueue(item.id,{status:"blocked",locked_at:null,last_error:"Redirect rejected by public-network safety policy",updated_at:now()},"redirect rejected");return {blocked:1,fetched:0,added:0,discovered:0,errors:0};}
      if(!await robotsAllowed(nextUrl)){await updateQueue(item.id,{status:"blocked",locked_at:null,last_error:"Redirect destination disallows crawling or robots.txt could not be read",updated_at:now()},"redirect robots denied");return {blocked:1,fetched:0,added:0,discovered:0,errors:0};}
      url=nextUrl;
    }
    if(!response|| (response.status>=300&&response.status<400))throw new Error("Redirect chain exceeded safe limit");
    if(!response.ok||!/text\/html|application\/xhtml\+xml/i.test(response.headers.get("content-type")||""))throw new Error("Non-HTML response or HTTP "+response.status);
    const html=await readHtmlLimited(response);const page=extract(html,url.toString());const media=extractMedia(html,url.toString(),page.title,page.description);
    if(page.content.length<80||page.title.length<3)throw new Error("Insufficient readable page text");
    const record={url:url.toString(),canonical_url:url.toString(),host:url.hostname,tentacle:LANES.includes(item.tentacle)?item.tentacle:"general",title:page.title,description:page.description,content_text:page.content,content_type:response.headers.get("content-type"),http_status:response.status,fetched_at:now(),updated_at:now()};
    const inserted=await db.from("eh_pages").insert(record).select("id").maybeSingle();
    let added=0;
    if(!inserted.error&&inserted.data)added=1;
    else if(inserted.error?.code==="23505"){const upd=await db.from("eh_pages").update({...record,updated_at:now()}).eq("url",record.url);if(upd.error)throw upd.error;}
    else if(inserted.error)throw inserted.error;
    if(media.length){const mr=await db.from("eh_media").upsert(media,{onConflict:"page_url,media_url",ignoreDuplicates:true});if(mr.error)throw mr.error;}
    await updateQueue(item.id,{status:"done",attempts:(item.attempts||0)+1,locked_at:null,last_error:null,updated_at:now()},"mark done");
    let discovered=0;
    if(item.depth<5&&page.links.length){
      const validated=await Promise.all(page.links.slice(0,6).map(raw=>safeUrl(raw,url.toString())));
      const candidates=validated.filter((link):link is URL=>link!==null).map(link=>({url:link.toString(),host:link.hostname,tentacle:record.tentacle,depth:item.depth+1,discovered_from:url.toString(),status:"pending"}));
      if(candidates.length){const q=await db.from("eh_crawl_queue").upsert(candidates,{onConflict:"url",ignoreDuplicates:true}).select("id");if(q.error)throw q.error;discovered=q.data?.length||0;}
    }
    return {blocked:0,fetched:1,added,discovered,errors:0};
  }catch(e){
    try { await retryOrFail(item,String(e)); }
    catch(retryError) { console.error("Queue retry/fail persistence failed",item.id,String(retryError)); }
    return {blocked:0,fetched:0,added:0,discovered:0,errors:1};
  }
  finally {
    // Delete by the original claimed host and queue id; do not hide cleanup errors.
    const released = await db.from("eh_host_leases").delete().eq("host", item.host).eq("queue_id", item.id);
    if (released.error) throw new Error("Host lease release failed for "+item.id+": "+released.error.message);
  }
}
Deno.serve(async(req:Request)=>{
  if(req.method!=="POST")return Response.json({error:"POST required"},{status:405});
  const started=now();
  // Sitemap discovery runs independently on its own schedule so page crawling never waits for it.
  const {data:seeds,error:seedErr}=await db.from("eh_seeds").select("url,host,tentacle").eq("enabled",true).order("priority").limit(250);
  if(seedErr)return Response.json({error:seedErr.message},{status:500});
  if(seeds?.length){const rows=seeds.map(s=>({url:s.url,host:s.host,tentacle:s.tentacle||"general",depth:0,status:"pending"}));const s=await db.from("eh_crawl_queue").upsert(rows,{onConflict:"url",ignoreDuplicates:true});if(s.error)return Response.json({error:s.error.message},{status:500});}
  const claimed=await db.rpc("claim_eh_crawl_batch",{batch_size:4});
  if(claimed.error)return Response.json({error:claimed.error.message},{status:500});
  const items=claimed.data||[];let cursor=0;const totals={blocked:0,fetched:0,new_pages:0,discovered:0,errors:0};
  const workers=Array.from({length:Math.min(2,items.length)},async()=>{
    while(true){const i=cursor++;if(i>=items.length)return;const r=await process(items[i]).catch(e=>{console.error("Worker failed",items[i].id,String(e));return {blocked:0,fetched:0,added:0,discovered:0,errors:1};});totals.blocked+=r.blocked;totals.fetched+=r.fetched;totals.new_pages+=r.added;totals.discovered+=r.discovered;totals.errors+=r.errors;}
  });
  await Promise.all(workers);
  const duration=Math.round((Date.now()-Date.parse(started))/1000);
  const run=await db.from("eh_crawl_runs").insert({started_at:started,finished_at:now(),seeds_loaded:seeds?.length||0,pages_fetched:totals.fetched,urls_discovered:totals.discovered,errors:totals.errors,status:"completed",notes:JSON.stringify({claimed:items.length,successful_fetches:totals.fetched,unique_new_pages:totals.new_pages,blocked:totals.blocked,duration_seconds:duration,batch_size:4,concurrency:2})}).select("id").maybeSingle();
  if(run.error) return Response.json({error:"Crawl run record failed: "+run.error.message,claimed:items.length,...totals,duration_seconds:duration},{status:500});
  return Response.json({claimed:items.length,...totals,duration_seconds:duration,run_id:run.data?.id});
});