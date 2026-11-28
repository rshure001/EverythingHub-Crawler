import "jsr:@supabase/functions-js/edge-runtime.d.ts";
const html = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Everything Hub — Everything within reach</title>
<meta name="description" content="Everything within reach. Search the web, news, images, videos, shopping, places, business and research.">
<style>
:root{color-scheme:dark;font-family:Inter,system-ui,Arial,sans-serif;background:#020a10;color:#f4fff8}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:radial-gradient(ellipse at 50% 15%,#073a35 0,#03151d 48%,#01060b 100%)}
header{height:66px;display:flex;align-items:center;justify-content:center;border-bottom:1px solid #2bff9a66;background:#020a10dd}
.brand{font-size:24px;font-weight:900;letter-spacing:-1px}.brand span{color:#b6ff43}
main{width:min(1120px,calc(100% - 28px));margin:22px auto 48px}
.hero{border:1px solid #32ff9a;border-radius:24px;padding:18px 18px 26px;text-align:center;background:radial-gradient(ellipse at 50% 45%,#0a4940,#041d25 65%,#020b12);box-shadow:0 0 24px #00ff9a45,inset 0 0 38px #00aaff16}
.kicker{font-size:11px;letter-spacing:3px;color:#b6ff43;font-weight:800}
h1{margin:10px 0 0;font-size:clamp(35px,6vw,64px);letter-spacing:-2px;color:#fff;text-shadow:0 0 20px #27ff9a55}
h1 em{font-family:Georgia,serif;color:#b6ff43}
.sub{margin:6px 0 0;color:#c9e9d9}
.art{width:min(560px,96%);height:260px;margin:0 auto;display:block;filter:drop-shadow(0 0 17px #00ff9a66)}
.search{display:flex;gap:8px;width:min(780px,100%);margin:2px auto 0;padding:7px;border:2px solid #47ff76;border-radius:999px;background:#fafffc;box-shadow:0 0 20px #27ff9a66}
.search input{flex:1;min-width:0;border:0;outline:0;background:transparent;color:#10251c;font-size:17px;padding:0 15px}
.search button{border:0;border-radius:999px;background:linear-gradient(135deg,#008d58,#8bea35);padding:0 25px;font-weight:900;color:#03170d;cursor:pointer}
.motto{margin:16px 0 0;color:#c7ff8c;font-size:13px;font-weight:800;letter-spacing:2px}
.categories{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:24px 0}
.tile{display:flex;align-items:center;gap:12px;padding:16px;border:1px solid #1aff9a88;border-radius:14px;background:linear-gradient(145deg,#092a30,#031018);color:#f4fff8;text-decoration:none;transition:.18s}
.tile:hover{transform:translateY(-2px);border-color:#b6ff43;box-shadow:0 0 16px #00ff9a55}
.ico{font-size:24px}.tile strong{display:block;font-size:14px}.tile small{display:block;color:#9bcbb2;font-size:10px;margin-top:3px}
.results{max-width:850px;margin:22px auto}.result{padding:14px 4px;border-bottom:1px solid #245347}.result a{color:#b6ff78;font-size:18px;text-decoration:none}.result p{color:#c3d7cc;margin:6px 0;font-size:13px}
footer{text-align:center;color:#82aa98;font-size:11px;padding:20px}
@media(max-width:650px){.hero{padding:15px 10px 20px;border-radius:18px}.art{height:205px}.categories{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.tile{padding:12px 10px}.search{height:54px}.search input{font-size:14px;padding:0 8px}.search button{padding:0 17px}h1{font-size:39px}}

.catalog-result{border:1px solid #32ff9a88;border-radius:15px;padding:15px;background:linear-gradient(145deg,#08262c,#031018);margin:14px 0}
.catalog-heading{display:flex;align-items:center;justify-content:space-between;gap:8px;color:#d9ffb7}
.catalog-heading strong{font-size:18px}.catalog-heading span,.matched-record span{font-size:10px;color:#9bcbb2;text-transform:uppercase;letter-spacing:1px}
.catalog-meta,.nested-meta,.rights-note{font-size:11px;color:#b6ff78}
.matched-record{margin:12px 0;padding:11px;border-left:2px solid #29d78d;background:#03131b}
.matched-record strong{color:#eafff5;font-size:14px}.matched-record p{font-size:12px;color:#c3d7cc}
.matched-record a,.catalog-result>a{display:inline-block;color:#b6ff78;font-size:12px;margin:3px 10px 3px 0}
.open-record-catalog,.more-records{border:1px solid #8bea35;border-radius:999px;background:#0a4b39;color:#f4fff8;padding:8px 12px;cursor:pointer;font-weight:700;margin:6px 6px 6px 0}
.open-record-catalog:disabled,.more-records:disabled{opacity:.6;cursor:wait}
.nested{margin:10px 0 0 10px;padding-left:12px;border-left:1px solid #1aff9a55}
.section-title{color:#b6ff78;font-size:18px;margin:22px 0 10px}
</style></head><body>
<header><div class="brand">EVERYTHING <span>HUB</span></div></header>
<main><section class="hero"><div class="kicker">ONE HUB · ENDLESS DISCOVERY</div>
<h1>Everything <em>within reach.</em></h1>
<p class="sub">One search. Every direction.</p>
<svg class="art" viewBox="0 0 560 270" role="img" aria-label="Emerald octopus with glowing brain and eight tentacles">
<defs><radialGradient id="brain"><stop stop-color="#f3ffb0"/><stop offset=".45" stop-color="#66ff85"/><stop offset="1" stop-color="#087c59"/></radialGradient><linearGradient id="skin" x2="0" y2="1"><stop stop-color="#8bff8c"/><stop offset=".5" stop-color="#12c879"/><stop offset="1" stop-color="#006c61"/></linearGradient><filter id="glow"><feGaussianBlur stdDeviation="9" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>
<g fill="none" stroke="url(#skin)" stroke-width="24" stroke-linecap="round" filter="url(#glow)">
<path d="M245 155 C175 105 140 80 85 36"/><path d="M250 170 C175 165 120 165 40 125"/><path d="M255 185 C185 215 135 230 65 245"/><path d="M275 195 C250 240 230 250 205 262"/>
<path d="M315 155 C385 105 420 80 475 36"/><path d="M310 170 C385 165 440 165 520 125"/><path d="M305 185 C375 215 425 230 495 245"/><path d="M285 195 C310 240 330 250 355 262"/></g>
<ellipse cx="280" cy="151" rx="91" ry="81" fill="url(#skin)" stroke="#9dffad" stroke-width="4"/>
<path d="M222 118 Q280 55 338 118 Q326 70 280 62 Q234 70 222 118" fill="url(#brain)" stroke="#c5ff9d" stroke-width="3" filter="url(#glow)"/>
<g fill="#f5fff5"><ellipse cx="248" cy="157" rx="14" ry="18"/><ellipse cx="312" cy="157" rx="14" ry="18"/></g><g fill="#06231c"><circle cx="252" cy="160" r="6"/><circle cx="308" cy="160" r="6"/></g>
<path d="M260 188 Q280 202 300 188" fill="none" stroke="#063e32" stroke-width="5" stroke-linecap="round"/>
<g fill="#d5ffb5" opacity=".8"><circle cx="91" cy="48" r="5"/><circle cx="61" cy="130" r="5"/><circle cx="79" cy="236" r="5"/><circle cx="210" cy="254" r="5"/><circle cx="469" cy="48" r="5"/><circle cx="499" cy="130" r="5"/><circle cx="481" cy="236" r="5"/><circle cx="350" cy="254" r="5"/></g></svg>
<form class="search" id="search"><input id="q" type="search" placeholder="What are you looking for?" aria-label="Search Everything Hub"><button>SEARCH</button></form>
<div class="motto">DISCOVER MORE. DO MORE.</div></section>
<nav class="categories" aria-label="Search categories">
<a class="tile" data-mode="web" href="#"><span class="ico">🌐</span><span><strong>Web</strong><small>Explore the internet</small></span></a>
<a class="tile" data-mode="news" href="#"><span class="ico">📰</span><span><strong>News</strong><small>What's happening</small></span></a>
<a class="tile" data-mode="images" href="#"><span class="ico">🖼️</span><span><strong>Images</strong><small>Visual discovery</small></span></a>
<a class="tile" data-mode="videos" href="#"><span class="ico">▶️</span><span><strong>Videos</strong><small>Watch and explore</small></span></a>
<a class="tile" data-mode="shopping" href="#"><span class="ico">🛍️</span><span><strong>Shopping</strong><small>Find products</small></span></a>
<a class="tile" data-mode="places" href="#"><span class="ico">📍</span><span><strong>Places</strong><small>Discover locations</small></span></a>
<a class="tile" data-mode="business" href="#"><span class="ico">🏢</span><span><strong>Business</strong><small>Companies and services</small></span></a>
<a class="tile" data-mode="research" href="#"><span class="ico">🔬</span><span><strong>Research</strong><small>Knowledge and learning</small></span></a></nav>
<section class="results" id="results" aria-live="polite"></section></main><footer>EVERYTHING HUB · EVERYTHING WITHIN REACH · <a href="https://DogTagDay.org" target="_blank" rel="noopener noreferrer" style="color:#b6ff78">DogTagDay.org</a></footer>
<script>
const form=document.querySelector('#search'),input=document.querySelector('#q'),out=document.querySelector('#results');
const base='https://vdceayvfgkwpgwixrhwi.supabase.co/functions/v1/';
let mode='web',activeController=null,nestedCounter=0;
const laneMap={web:'general',news:'news',business:'business',research:'science',shopping:'business',places:'general'};
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function safe(u){try{const x=new URL(u);return ['http:','https:'].includes(x.protocol)?x.href:'#'}catch{return '#'}}
function sourceLink(u,label){const href=safe(u);return href==='#'?'':'<a target="_blank" rel="noopener noreferrer" href="'+esc(href)+'">'+esc(label||'Open source')+'</a>'}
function recordCard(r){
 const id=String(r.record_catalog_id||r.record_id||'');
 const ids=Array.isArray(r.shared_catalog_ids)?r.shared_catalog_ids.slice(0,1):[];
 const target='nested-'+(++nestedCounter);
 const source=Array.isArray(r.source_urls)?r.source_urls[0]:(r.source_url||'');
 const button=id&&ids.length?'<button class="open-record-catalog" data-record-catalog-id="'+esc(id)+'" data-catalog-ids="'+esc(ids.join(','))+'" data-target="'+target+'">Open this entry catalog</button><div class="nested" id="'+target+'"></div>':'';
 return '<div class="matched-record"><strong>'+esc(r.title||'Untitled record')+'</strong> <span>'+esc(r.record_type||'record')+'</span><p>'+esc(r.description||'')+'</p>'+sourceLink(source,'Open source')+button+'</div>';
}
function catalogCard(c){
 const records=(c.matched_records||[]).slice(0,3);
 const count=Number(c.record_count||0),pages=Number(c.source_page_count||0);
 const pageLabel=c.catalog_type==='query-derived page collection'?'candidate pages scanned':'source pages';
 return '<article class="result catalog-result"><div class="catalog-heading"><strong>'+esc(c.catalog_title||c.title||'Catalog')+'</strong><span>'+esc(c.catalog_type||'catalog')+'</span></div><p class="catalog-meta">'+count.toLocaleString()+' records · '+pages.toLocaleString()+' '+pageLabel+' · candidate preview only</p><p>'+esc(c.snippet||c.description||'')+'</p>'+sourceLink(c.url,'Open source page')+(records.length?'<div class="matched-records"><h3>Matching entries</h3>'+records.map(recordCard).join('')+'</div>':'')+'<p class="rights-note">Source terms and individual record rights have not been cleared for redistribution.</p></article>';
}
function resultCard(r){return '<article class="result"><a target="_blank" rel="noopener noreferrer" href="'+esc(safe(r.url||r.pageUrl))+'">'+esc(r.title||'Open result')+'</a><p>'+esc(r.snippet||r.description||'')+'</p><span class="source">'+esc(r.source||r.origin||r.provider||'Search result')+'</span></article>'}
async function loadNested(button){
 const target=document.getElementById(button.dataset.target);if(!target)return;
 const recordId=String(button.dataset.recordCatalogId||'');
 const ids=String(button.dataset.catalogIds||'').split(',').filter(Boolean).slice(0,3);
 const offset=Math.max(0,Number(button.dataset.offset||0));
 if(!recordId||!ids.length)return;
 const oldText=button.textContent;button.disabled=true;button.textContent='Loading catalog…';
 if(offset===0)target.innerHTML='<p class="nested-meta">Opening this entry catalog…</p>';
 try{
  const params=new URLSearchParams({record_catalog_id:recordId,catalog_ids:ids.join(','),limit:'10',offset:String(offset)});
  const response=await fetch(base+'everything-hub-web-search?'+params.toString(),{headers:{'Accept':'application/json'}});
  if(!response.ok)throw new Error('Nested catalog returned '+response.status);
  const data=await response.json(),rows=data.related_records||[];
  const markup=rows.map(recordCard).join('');
  if(offset===0)target.innerHTML='<p class="nested-meta">'+Number(data.related_count||0).toLocaleString()+' entries in this shared catalog · preview only</p>'+markup;
  else{const oldMore=target.querySelector('.more-records');if(oldMore)oldMore.remove();target.insertAdjacentHTML('beforeend',markup)}
  if(data.has_more&&rows.length){
   target.insertAdjacentHTML('beforeend','<button class="more-records" data-record-catalog-id="'+esc(recordId)+'" data-catalog-ids="'+esc(ids.join(','))+'" data-target="'+esc(button.dataset.target)+'" data-offset="'+(offset+rows.length)+'">Load more entries</button>');
  }
  if(!rows.length&&offset===0)target.insertAdjacentHTML('beforeend','<p>No related entries found in this catalog.</p>');
 }catch(error){target.innerHTML='<p class="nested-meta">This entry catalog is temporarily unavailable. Try again.</p>'}
 finally{button.disabled=false;button.textContent=oldText}
}
out.addEventListener('click',event=>{
 const button=event.target.closest('button.open-record-catalog,button.more-records');
 if(!button)return;
 event.preventDefault();
 loadNested(button);
});
async function go(m){
 mode=m;const q=input.value.trim();if(q.length<2){input.focus();return}
 if(activeController)activeController.abort();activeController=new AbortController();
 out.innerHTML='<p style="color:#b6ff78;text-align:center">Searching Everything Hub…</p>';
 try{
  if(m==='images'||m==='videos'){
   const params=new URLSearchParams({q,type:m==='videos'?'video':'image'});
   const response=await fetch(base+'everything-hub-media-search?'+params.toString(),{signal:activeController.signal,headers:{'Accept':'application/json'}});
   if(!response.ok)throw new Error('Media search returned '+response.status);
   const data=await response.json(),rows=data.results||[];
   out.innerHTML=rows.length?'<h2 class="section-title">'+rows.length+' '+esc(m)+' results for “'+esc(q)+'”</h2>'+rows.map(resultCard).join(''):'<p>No indexed '+esc(m)+' results found yet.</p>';
   return;
  }
  const catalogParams=new URLSearchParams({q,limit:'5'});
  const webParams=new URLSearchParams({q,limit:'20',tentacle:laneMap[m]||'general'});
  const requests=await Promise.all([
   fetch(base+'everything-hub-catalog-search?'+catalogParams.toString(),{signal:activeController.signal,headers:{'Accept':'application/json'}}).then(r=>r.ok?r.json():null).catch(()=>null),
   fetch(base+'everything-hub-web-search?'+webParams.toString(),{signal:activeController.signal,headers:{'Accept':'application/json'}}).then(r=>r.ok?r.json():null).catch(()=>null)
  ]);
  const catalogData=requests[0],webData=requests[1];
  const catalogs=catalogData&&Array.isArray(catalogData.catalogResults)?catalogData.catalogResults:[];
  const rows=webData&&Array.isArray(webData.results)?webData.results:[];
  const preview=catalogData&&catalogData.catalogPreview;
  const freshness=preview?'<p class="rights-note">Catalog inventory: '+Number(preview.catalogCount||0).toLocaleString()+' catalogs · '+Number(preview.recordCount||0).toLocaleString()+' candidate records. Not yet published as verified records.</p>':'';
  const catalogMarkup=catalogs.length?'<h2 class="section-title">Catalogs matching “'+esc(q)+'”</h2>'+catalogs.map(catalogCard).join('')+freshness:'';
  const webMarkup=rows.length?'<h2 class="section-title">Web results</h2>'+rows.map(resultCard).join(''):'';
  out.innerHTML=catalogMarkup+webMarkup||'<p style="color:#c3d7cc">No indexed results found yet. Try a broader search.</p>';
 }catch(error){if(error.name==='AbortError')return;out.innerHTML='<p style="color:#ffc78b">Search is temporarily unavailable. Please try again.</p>'}
}
form.addEventListener('submit',e=>{e.preventDefault();go(mode)});
document.querySelectorAll('.tile').forEach(a=>a.addEventListener('click',e=>{e.preventDefault();go(a.dataset.mode)}));
const incoming=new URLSearchParams(location.search);if(incoming.get('q')){input.value=incoming.get('q');go(incoming.get('mode')||'web')}
</script></body></html>`;
Deno.serve((req: Request) => {
  if (req.method !== "GET" && req.method !== "HEAD") return new Response("Method not allowed", {status:405,headers:{"Allow":"GET, HEAD"}});
  return new Response(req.method === "HEAD" ? null : html, {status:200,headers:{"Content-Type":"text/html; charset=utf-8","Cache-Control":"no-store","X-Content-Type-Options":"nosniff","Content-Disposition":"inline"}});
});