const input=document.getElementById('query'),form=document.getElementById('searchForm'),status=document.getElementById('status');
let mode=new URLSearchParams(location.search).get('mode')||'web';
document.querySelectorAll('[data-mode]').forEach(b=>b.addEventListener('click',()=>{mode=b.dataset.mode;input.focus();status.textContent='Selected '+b.getAttribute('aria-label')+' search';}));
const resultsRoot=document.getElementById('results');
function safeUrl(value){try{const u=new URL(value);return (u.protocol==='https:'||u.protocol==='http:')?u.href:'#';}catch{return '#';}}
function makeHeading(q,label,countText){
  const heading=document.createElement('div');heading.className='results-heading';
  const h=document.createElement('h2');h.textContent=label+' “'+q+'”';
  const count=document.createElement('p');count.textContent=countText;
  heading.append(h,count);resultsRoot.append(heading);
}
function renderResults(data,q){
  resultsRoot.replaceChildren();
  const visibleCount=Array.isArray(data.results)?data.results.length:(Number(data.count)||0);
  const indexedCount=Number(data.indexedPages)||0;
  makeHeading(q,'Results for',visibleCount.toLocaleString()+' visible results'+(indexedCount?' · '+indexedCount.toLocaleString()+' indexed pages':''));
  const list=document.createElement('div');list.className='results-list';
  for(const item of (Array.isArray(data.results)?data.results:[])){
    const card=document.createElement('article');card.className='eh-result';
    const link=document.createElement('a');link.href=safeUrl(item.url);link.target='_blank';link.rel='noopener noreferrer';link.textContent=item.title||item.url||'Web result';
    const snippet=document.createElement('p');snippet.textContent=item.snippet||item.description||'';
    const source=document.createElement('small');source.textContent=(item.source||'Public web')+(item.origin?' · '+item.origin:'');
    card.append(link,snippet,source);list.append(card);
  }
  if(!list.children.length){const empty=document.createElement('div');empty.className='results-empty';empty.textContent='No matching results came back. Try another search or category.';list.append(empty);}
  resultsRoot.append(list);
  if(data.catalogPreview&&data.catalogPreview.status!=='ready'){const note=document.createElement('p');note.className='results-note';note.textContent='Catalog entries are provisional until their source and reuse terms are verified.';resultsRoot.append(note);}
}
function cleanCommonsValue(value){
  return String(value||'').replace(/<[^>]*>/g,' ')
    .replace(/&amp;/g,'&').replace(/&lt;/g,'<').replace(/&gt;/g,'>')
    .replace(/&quot;/g,'"').replace(/&apos;/g,"'")
    .replace(/&#(\d+);/g,(_,n)=>String.fromCodePoint(Number(n)))
    .replace(/&#x([0-9a-f]+);/gi,(_,n)=>String.fromCodePoint(parseInt(n,16)))
    .replace(/\s+/g,' ').trim();
}
function mediaPreviewMetadata(meta){
  const license=cleanCommonsValue(meta.LicenseShortName?.value);
  const licenseUrl=cleanCommonsValue(meta.LicenseUrl?.value);
  const creator=cleanCommonsValue(meta.Artist?.value||meta.Credit?.value||'');
  const raw=cleanCommonsValue(meta.AttributionRequired?.value).toLowerCase();
  const attributionRequired=raw==='true'?true:raw==='false'?false:null;
  const publicDomain=license.toLowerCase().startsWith('public domain');
  const cc0=/^cc0(?:\s|$)/i.test(license);
  const ccByMatch=license.match(/^cc by(-sa)?\s+(1\.0|2\.0|2\.5|3\.0|4\.0)$/i);
  let ccByLicenseUrlValid=false;
  if(ccByMatch){try{
    const url=new URL(licenseUrl),kind=ccByMatch[1]?'by-sa':'by';
    const expected='/licenses/'+kind+'/'+ccByMatch[2]+'/';
    ccByLicenseUrlValid=url.protocol==='https:'&&url.hostname==='creativecommons.org'&&url.pathname.replace(/\/+$/,'/')===expected;
  }catch{}}
  const licenseValid=publicDomain||cc0||(ccByMatch!==null&&ccByLicenseUrlValid);
  const attributionValid=ccByMatch?creator.length>0:(attributionRequired===false||(attributionRequired===true&&creator.length>0));
  return {allowed:licenseValid&&attributionValid,license,licenseUrl,creator,attributionRequired};
}
function renderImages(data,q){
  resultsRoot.replaceChildren();
  const candidates=Object.values(data.query?.pages||{}).filter(p=>p.imageinfo&&p.imageinfo[0]?.thumburl);
  const pages=candidates.map(page=>({page,preview:mediaPreviewMetadata(page.imageinfo[0].extmetadata||{})}))
    .filter(entry=>entry.preview.allowed).slice(0,24);
  makeHeading(q,'Image results',pages.length.toLocaleString()+' licensed Wikimedia Commons images');
  const grid=document.createElement('div');grid.className='media-grid';
  for(let index=0;index<pages.length;index++){
    const p=pages[index].page,info=p.imageinfo[0],preview=pages[index].preview;
    const card=document.createElement('article');card.className='media-card';
    card.dataset.license=preview.license;card.dataset.creator=preview.creator;
    const link=document.createElement('a');link.href=safeUrl(info.descriptionurl);link.target='_blank';link.rel='noopener noreferrer';
    const img=document.createElement('img');img.src=safeUrl(info.thumburl);img.alt=p.title||q;
    img.loading=index<8?'eager':'lazy';img.decoding='async';img.referrerPolicy='no-referrer';
    img.onerror=()=>{const placeholder=document.createElement('div');placeholder.className='image-placeholder';placeholder.textContent='Preview unavailable';img.replaceWith(placeholder);};
    const title=document.createElement('a');title.href=safeUrl(info.descriptionurl);title.target='_blank';title.rel='noopener noreferrer';title.textContent=(p.title||'Image').replace(/^File:/,'');
    const small=document.createElement('small');small.append(document.createTextNode('Wikimedia Commons · '));
    if(preview.creator)small.append(document.createTextNode(preview.creator+' · '));
    if(preview.licenseUrl&&safeUrl(preview.licenseUrl)!=='#'){
      const licenseLink=document.createElement('a');licenseLink.href=safeUrl(preview.licenseUrl);licenseLink.target='_blank';licenseLink.rel='noopener noreferrer';licenseLink.textContent=preview.license||'License';small.append(licenseLink);
    }else small.append(document.createTextNode(preview.license||'Public domain'));
    link.append(img);card.append(link,title,small);grid.append(card);
  }
  if(!pages.length){const empty=document.createElement('div');empty.className='results-empty';empty.textContent='No matching Commons images with a verified reusable license and attribution were found. Try another search.';resultsRoot.append(empty);return;}
  resultsRoot.append(grid);
}
function renderVideos(data,q){
  resultsRoot.replaceChildren();
  const items=Array.isArray(data.items)?data.items:[];
  makeHeading(q,'Video results',items.length.toLocaleString()+' videos · '+(data.provider||'public video search'));
  const grid=document.createElement('div');grid.className='video-grid';
  for(const item of items){
    const card=document.createElement('article');card.className='video-card';
    const link=document.createElement('a');link.href=safeUrl(item.url);link.target='_blank';link.rel='noopener noreferrer';
    if(item.thumbnail){const img=document.createElement('img');img.src=item.videoId?'https://everythinghub-search-backend.vercel.app/api/video-thumbnail?id='+encodeURIComponent(item.videoId):safeUrl(item.thumbnail);img.alt=item.title||q;img.loading='eager';img.referrerPolicy='no-referrer';img.onerror=()=>{if(item.thumbnail&&img.src!==safeUrl(item.thumbnail)){img.src=safeUrl(item.thumbnail);img.onerror=()=>{const p=document.createElement('div');p.className='video-placeholder';p.textContent='▶ Watch video';img.replaceWith(p);};}else{const p=document.createElement('div');p.className='video-placeholder';p.textContent='▶ Watch video';img.replaceWith(p);}};link.append(img);}else{const placeholder=document.createElement('div');placeholder.className='results-empty';placeholder.textContent='▶ Video';link.append(placeholder);}
    const title=document.createElement('a');title.href=safeUrl(item.url);title.target='_blank';title.rel='noopener noreferrer';title.textContent=item.title||'Video';
    const small=document.createElement('small');small.textContent=(item.source||'Video source')+(item.duration?' · '+item.duration:'')+(item.views?' · '+item.views:'');
    card.append(link,title,small);grid.append(card);
  }
  if(!items.length){const empty=document.createElement('div');empty.className='results-empty';empty.textContent='No videos were returned for this search.';resultsRoot.append(empty);return;}
  resultsRoot.append(grid);
}
async function runImageSearch(q){
  status.textContent='Searching public image collections…';
  const u=new URL('https://commons.wikimedia.org/w/api.php');
  for(const [k,v] of Object.entries({action:'query',generator:'search',gsrsearch:q,gsrnamespace:'6',gsrlimit:'50',prop:'imageinfo',iiprop:'url|extmetadata',iiurlwidth:'600',format:'json',origin:'*'}))u.searchParams.set(k,v);
  const response=await fetch(u,{signal:AbortSignal.timeout(20000)});if(!response.ok)throw new Error('Image provider HTTP '+response.status);
  const data=await response.json();renderImages(data,q);
  const n=document.querySelectorAll('#results .media-card').length;status.textContent=n.toLocaleString()+' licensed public images from Wikimedia Commons.';
}
async function runVideoSearch(q){
  status.textContent='Searching public video sources…';
  const u=new URL('https://everythinghub-search-backend.vercel.app/api/videos');u.searchParams.set('q',q);
  const response=await fetch(u,{signal:AbortSignal.timeout(25000)});const data=await response.json();
  if(!response.ok||data.status!=='ok')throw new Error(data.message||'Video search unavailable');
  renderVideos(data,q);status.textContent=data.count.toLocaleString()+' videos from '+data.provider+'.';
}
async function runSearch(){
  const q=input.value.trim();if(!q){input.focus();return;}
  status.textContent='Searching…';resultsRoot.replaceChildren();
  const pageUrl=new URL(location.href);pageUrl.searchParams.set('q',q);pageUrl.searchParams.set('mode',mode);history.replaceState({},'',pageUrl);
  try{
    if(mode==='images'){await runImageSearch(q);return;}
    if(mode==='videos'){await runVideoSearch(q);return;}
    const u=new URL('https://everythinghub-search-backend.vercel.app/api/search');u.searchParams.set('q',q);u.searchParams.set('limit','20');u.searchParams.set('lane',mode);
    let data=null;
    try{const response=await fetch(u,{headers:{accept:'application/json'},signal:AbortSignal.timeout(6000)});if(response.ok){const candidate=await response.json();if(Array.isArray(candidate.results)&&candidate.results.length)data=candidate;}}catch(error){}
    if(!data){const fast=new URL('https://everythinghub-search-backend.vercel.app/api/search-fast');fast.searchParams.set('q',q);fast.searchParams.set('lane',mode);const response=await fetch(fast,{headers:{accept:'application/json'},signal:AbortSignal.timeout(12000)});if(!response.ok)throw new Error('Live web search returned HTTP '+response.status);data=await response.json();if(data.status!=='ok'||!Array.isArray(data.results)||!data.results.length)throw new Error(data.message||'No public results returned');}
    renderResults(data,q);const visibleCount=Array.isArray(data.results)?data.results.length:(Number(data.count)||0);status.textContent=visibleCount.toLocaleString()+' visible results from '+(data.providers||[]).join(' and ')+'.';
  }catch(error){status.textContent=mode==='images'?'Image search is temporarily unavailable.':mode==='videos'?'Video sources are temporarily unavailable.':'Search is temporarily unavailable. Please try again.';const message=document.createElement('div');message.className='results-empty';message.textContent=error.message||'The search service did not respond. Please try again.';resultsRoot.replaceChildren(message);}
}
const newsItems=document.getElementById('news-items'),newsStatus=document.getElementById('news-status');
let activeNewsCategory='for-you';
async function loadNews(category){
  activeNewsCategory=category;newsStatus.textContent='Loading the latest stories…';newsItems.replaceChildren();
  document.querySelectorAll('[data-news]').forEach(b=>b.classList.toggle('active',b.dataset.news===category));
  try{
    const u=new URL('https://everythinghub-search-backend.vercel.app/api/news');u.searchParams.set('category',category);
    const response=await fetch(u,{signal:AbortSignal.timeout(15000)});const data=await response.json();
    if(!response.ok||data.status!=='ok'||!Array.isArray(data.items)||!data.items.length)throw new Error(data.message||'No news stories returned');
    newsStatus.textContent='Updated '+new Date(data.updatedAt).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'})+' · '+data.count+' stories';
    for(const item of data.items){
      const card=document.createElement('article');card.className='news-item';
      const link=document.createElement('a');link.href=safeUrl(item.url);link.target='_blank';link.rel='noopener noreferrer';link.textContent=item.title;
      const meta=document.createElement('p');meta.textContent=(item.source||'News source')+(item.publishedAt?' · '+new Date(item.publishedAt).toLocaleString([],{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}):'');
      card.append(link,meta);newsItems.append(card);
    }
  }catch(error){newsStatus.textContent='News sources are temporarily unavailable. Try Refresh.';const msg=document.createElement('div');msg.className='results-empty';msg.textContent='The news feed did not respond. Tap Refresh to try again.';newsItems.replaceChildren(msg);}
}
document.querySelectorAll('[data-news]').forEach(b=>b.addEventListener('click',()=>void loadNews(b.dataset.news)));
document.getElementById('news-refresh').addEventListener('click',()=>void loadNews(activeNewsCategory));
void loadNews(activeNewsCategory);
form.addEventListener('submit',e=>{e.preventDefault();void runSearch();});
input.addEventListener('input',()=>form.classList.toggle('has-query',input.value.trim().length>0));
const params=new URLSearchParams(location.search);if(params.get('q')){input.value=params.get('q');void runSearch();}
