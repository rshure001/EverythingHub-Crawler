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
function renderCatalogResults(items,q){
  const safeItems=(Array.isArray(items)?items:[]).filter(item=>
    item&&item.preview_only===true&&item.commercial_eligible===false&&safeUrl(item.url)!=='#'
  ).slice(0,5);
  if(!safeItems.length)return 0;
  makeHeading(q,'Catalog matches',safeItems.length.toLocaleString()+' candidate previews · preview only');
  const list=document.createElement('div');list.className='catalog-preview-list';
  for(const item of safeItems){
    const card=document.createElement('article');card.className='eh-catalog-result';
    card.dataset.catalogId=String(item.catalog_id||'');
    card.dataset.relevanceClass=String(item.relevance_class||'');
    card.dataset.previewOnly='true';
    card.dataset.commercialEligible='false';
    const meta=document.createElement('div');meta.className='catalog-meta';
    const kind=document.createElement('span');kind.className='catalog-pill';
    kind.textContent=String(item.catalog_type||item.record_type||'catalog').replace(/_/g,' ');
    const relevance=document.createElement('span');relevance.className='catalog-relevance';
    relevance.textContent=String(item.relevance_label||'Catalog record match');
    meta.append(kind,relevance);
    const link=document.createElement('a');link.href=safeUrl(item.url);link.target='_blank';link.rel='noopener noreferrer';
    link.textContent=String(item.catalog_title||item.title||'Untitled catalog');
    const snippet=document.createElement('p');snippet.textContent=String(item.snippet||item.description||'Candidate catalog preview.');
    const counts=document.createElement('small');counts.className='catalog-counts';
    const recordCount=Number.isSafeInteger(item.record_count)?item.record_count:0;
    const pageCount=Number.isSafeInteger(item.source_page_count)?item.source_page_count:0;
    counts.textContent=recordCount.toLocaleString()+' records · '+pageCount.toLocaleString()+' source pages';
    const matched=document.createElement('small');matched.className='catalog-matched';
    const matchedRecords=Array.isArray(item.matched_records)?item.matched_records.slice(0,3):[];
    matched.textContent=matchedRecords.length
      ?'Matched records: '+matchedRecords.map(record=>String(record.record_type||'record')+': '+String(record.title||'Untitled')).join(' · ')
      :'Matched record titles are not available in this preview.';
    const rights=document.createElement('p');rights.className='catalog-rights';
    const terms=String(item.source_terms_status||'not_reviewed');
    const clearance=String(item.record_level_clearance||'not_reviewed');
    const termsText=terms==='not_reviewed'?'Source terms not reviewed':
      terms==='reviewed_per_dataset'?'Source terms reviewed per dataset':
      'Source policy status: '+terms.replace(/_/g,' ');
    const rightsText=clearance==='not_reviewed'?'Record rights not cleared':
      'Record rights: '+clearance.replace(/_/g,' ');
    const redistribution=String(item.rights_status||'').includes('not_cleared_for_redistribution')
      ?' · Redistribution not cleared':'';
    rights.textContent='Candidate preview only · '+termsText+' · '+rightsText+
      ' · Commercial use not cleared'+redistribution;
    card.append(meta,link,snippet,counts,matched,rights);
    list.append(card);
  }
  resultsRoot.append(list);
  return safeItems.length;
}
function renderResults(data,q){
  resultsRoot.replaceChildren();
  const catalogCount=renderCatalogResults(data.catalogResults,q);
  const visibleCount=Array.isArray(data.results)?data.results.length:(Number(data.count)||0);
  const indexedCount=Number(data.indexedPages)||0;
  makeHeading(q,'Web results',visibleCount.toLocaleString()+' visible results'+(indexedCount?' · '+indexedCount.toLocaleString()+' indexed pages':''));
  const list=document.createElement('div');list.className='results-list';
  for(const item of (Array.isArray(data.results)?data.results:[])){
    const card=document.createElement('article');card.className='eh-result';
    const link=document.createElement('a');link.href=safeUrl(item.url);link.target='_blank';link.rel='noopener noreferrer';link.textContent=item.title||item.url||'Web result';
    const snippet=document.createElement('p');snippet.textContent=item.snippet||item.description||'';
    const source=document.createElement('small');source.textContent=(item.source||'Public web')+(item.origin?' · '+item.origin:'');
    card.append(link,snippet,source);list.append(card);
  }
  if(!list.children.length){const empty=document.createElement('div');empty.className='results-empty';empty.textContent='No matching web results came back. Try another search or category.';list.append(empty);}
  resultsRoot.append(list);
  if(!catalogCount&&data.catalogPreview&&data.catalogPreview.status!=='ready'){
    const note=document.createElement('p');note.className='results-note';
    note.textContent='No matching candidate catalog records were found in the current preview. Candidate records remain provisional until source terms and record-level rights are reviewed.';
    resultsRoot.append(note);
  }
}
function renderImages(data,q){
  resultsRoot.replaceChildren();
  const items=Array.isArray(data.items)?data.items:[];
  makeHeading(q,"Image results",items.length.toLocaleString()+" openly licensed images · Internet Archive");
  const grid=document.createElement("div");grid.className="media-grid";
  for(let index=0;index<items.length;index++){
    const item=items[index],card=document.createElement("article");card.className="media-card";
    card.dataset.license=String(item.license||"");card.dataset.creator=String(item.creator||"");
    const link=document.createElement("a");link.href=safeUrl(item.url);link.target="_blank";link.rel="noopener noreferrer";
    const img=document.createElement("img");img.src=safeUrl(item.thumbnail);img.alt=item.title||q;img.loading=index<8?"eager":"lazy";img.decoding="async";img.referrerPolicy="no-referrer";
    img.onerror=()=>{const placeholder=document.createElement("div");placeholder.className="image-placeholder";placeholder.textContent="Preview unavailable";img.replaceWith(placeholder);};
    link.append(img);
    const title=document.createElement("a");title.href=safeUrl(item.url);title.target="_blank";title.rel="noopener noreferrer";title.textContent=item.title||"Image";
    const small=document.createElement("small");small.append(document.createTextNode("Internet Archive · "+String(item.provider||item.source||"Internet Archive")+" · "));
    if(item.creator){
      if(safeUrl(item.creatorUrl)!=="#"){const creatorLink=document.createElement("a");creatorLink.href=safeUrl(item.creatorUrl);creatorLink.target="_blank";creatorLink.rel="noopener noreferrer";creatorLink.textContent=item.creator;small.append(creatorLink);}
      else small.append(document.createTextNode(item.creator));
      small.append(document.createTextNode(" · "));
    }
    const licenseLink=document.createElement("a");licenseLink.href=safeUrl(item.licenseUrl);licenseLink.target="_blank";licenseLink.rel="noopener noreferrer";licenseLink.textContent=item.license||"License";small.append(licenseLink);
    card.append(link,title,small);grid.append(card);
  }
  if(!items.length){const empty=document.createElement("div");empty.className="results-empty";empty.textContent="No matching Internet Archive images with an approved license and required attribution were found. Try another search.";resultsRoot.append(empty);return;}
  resultsRoot.append(grid);
}
function renderVideos(data,q){
  resultsRoot.replaceChildren();
  const items=Array.isArray(data.items)?data.items:[];
  makeHeading(q,"Video results",items.length.toLocaleString()+" videos · "+(data.provider||"Internet Archive"));
  const grid=document.createElement("div");grid.className="video-grid";
  for(const item of items){
    const card=document.createElement("article");card.className="video-card";
    const link=document.createElement("a");link.href=safeUrl(item.url);link.target="_blank";link.rel="noopener noreferrer";
    if(item.thumbnail){
      const img=document.createElement("img");img.src=safeUrl(item.thumbnail);img.alt=item.title||q;img.loading="eager";img.referrerPolicy="no-referrer";img.decoding="async";
      img.onerror=()=>{const placeholder=document.createElement("div");placeholder.className="video-placeholder";placeholder.textContent="▶ Watch video";img.replaceWith(placeholder);};
      link.append(img);
    }else{const placeholder=document.createElement("div");placeholder.className="video-placeholder";placeholder.textContent="▶ Watch video";link.append(placeholder);}
    const title=document.createElement("a");title.href=safeUrl(item.url);title.target="_blank";title.rel="noopener noreferrer";title.textContent=item.title||"Video";
    const small=document.createElement("small");small.append(document.createTextNode((item.source||"Video source")+" · "+(item.license||"License unavailable")+" · "+(item.creator||item.credit||"Creator not listed")+" · "));
    const policyLink=document.createElement("a");policyLink.href=safeUrl(item.licenseUrl||"https://creativecommons.org/");policyLink.target="_blank";policyLink.rel="noopener noreferrer";policyLink.textContent="license and reuse terms";
    small.append(policyLink);card.append(link,title,small);grid.append(card);
  }
  if(!items.length){const empty=document.createElement("div");empty.className="results-empty";empty.textContent="No videos with an approved item-level license and attribution were returned for this search.";resultsRoot.append(empty);return;}
  resultsRoot.append(grid);
}
async function runImageSearch(q){
  status.textContent="Searching open-license image collections…";
  const u=new URL("https://everythinghub-search-backend.vercel.app/api/images");u.searchParams.set("q",q);
  const response=await fetch(u,{signal:AbortSignal.timeout(25000)});const data=await response.json();
  if(!response.ok||data.status!=="ok")throw new Error(data.message||"Image search unavailable");
  renderImages(data,q);status.textContent=data.count.toLocaleString()+" openly licensed images from Internet Archive.";
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
    try{const response=await fetch(u,{headers:{accept:'application/json'},signal:AbortSignal.timeout(15000)});if(response.ok){const candidate=await response.json();if(Array.isArray(candidate.results)&&candidate.results.length)data=candidate;}}catch(error){}
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
