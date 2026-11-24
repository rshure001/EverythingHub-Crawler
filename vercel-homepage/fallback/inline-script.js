const input=document.getElementById('query'),form=document.getElementById('searchForm'),status=document.getElementById('status');
let mode=new URLSearchParams(location.search).get('mode')||'web';
document.querySelectorAll('[data-mode]').forEach(b=>b.addEventListener('click',()=>{mode=b.dataset.mode;input.focus();status.textContent='Selected '+b.getAttribute('aria-label')+' search';}));
const resultsRoot=document.getElementById('results');
const EH_SUPABASE_BASE='https://vdceayvfgkwpgwixrhwi.supabase.co/functions/v1';
const EH_PUBLIC_KEY='sb_publishable_XlIsACWhtVS1jVODWYGTpg_GMs8v3vJ';
const EH_SUPABASE_LANES={web:null,news:'news',images:'general',videos:'general',shopping:'business',places:'culture',business:'business',research:'science'};
async function fetchSupabaseFunction(slug,params={}){
  const u=new URL(EH_SUPABASE_BASE+'/'+slug);
  for(const [key,value] of Object.entries(params))if(value!==null&&value!==undefined&&value!=='')u.searchParams.set(key,String(value));
  const response=await fetch(u,{headers:{apikey:EH_PUBLIC_KEY,accept:'application/json'},signal:AbortSignal.timeout(15000)});
  const payload=await response.json().catch(()=>({}));
  if(!response.ok)throw new Error('Everything Hub free Edge service '+response.status);
  return payload;
}
async function requestSupabaseSearch(q,category){
  const payload=await fetchSupabaseFunction('everything-hub-web-search',{q,limit:20,offset:0,tentacle:EH_SUPABASE_LANES[category]});
  if(!hasIndexedResults(payload))return null;
  return {...payload,providers:Array.isArray(payload.providers)?payload.providers:[payload.provider||'Everything Hub Edge Search']};
}
async function requestSupabaseIndependentSearch(q){
  const payload=await fetchSupabaseFunction('everything-hub-independent-search',{q});
  return {...payload,results:Array.isArray(payload.results)?payload.results:[],count:Number(payload.count)||((payload.results||[]).length),provider:payload.provider||'Bing RSS',providers:[payload.provider||'Bing RSS']};
}
async function requestSupabaseMediaSearch(q,type){
  const payload=await fetchSupabaseFunction('everything-hub-media-search',{q,type});
  const licensed=(Array.isArray(payload.results)?payload.results:[]).filter(item=>
    item&&item.source==='Wikimedia Commons'&&item.license&&item.licenseUrl&&item.thumbnailUrl&&
    (type==='video'?item.videoUrl:item.imageUrl)
  );
  return {
    status:'ok',
    items:licensed.map(item=>({
      url:item.pageUrl||item.videoUrl||item.imageUrl,
      thumbnail:item.thumbnailUrl||item.imageUrl,
      title:item.title||'Licensed public media',
      source:item.source,
      license:item.license,
      licenseUrl:item.licenseUrl,
      creator:item.creator||item.credit,
      credit:item.credit||item.creator
    })),
    count:licensed.length,
    provider:'Wikimedia Commons'
  };
}

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
    const rawSourceKind=String(item.source_kind||'');
    const external=rawSourceKind==='external_link_only'||rawSourceKind==='curated_external_link_directory';
    const card=document.createElement('article');card.className='eh-catalog-result';
    card.dataset.catalogId=String(item.catalog_id||'');
    card.dataset.sourceKind=external?'external_link_only':(rawSourceKind||'page-derived');
    card.dataset.sourceOrigin=rawSourceKind;
    card.dataset.rightsStatus=String(item.rights_status||'not_cleared');
    card.dataset.relevanceClass=external?'external_link_only':String(item.relevance_class||'');
    card.dataset.previewOnly='true';
    card.dataset.commercialEligible='false';
    const meta=document.createElement('div');meta.className='catalog-meta';
    const kind=document.createElement('span');kind.className='catalog-pill';
    kind.textContent=external?'External directory · links only':String(item.catalog_type||item.record_type||'catalog').replace(/_/g,' ');
    const relevance=document.createElement('span');relevance.className='catalog-relevance';
    relevance.textContent=external?'Curated external links · not page-derived records':String(item.relevance_label||'Catalog record match');
    meta.append(kind,relevance);
    const link=document.createElement('a');link.href=safeUrl(item.url);link.target='_blank';link.rel='noopener noreferrer';
    link.textContent=String(item.catalog_title||item.title||'Untitled catalog');
    const snippet=document.createElement('p');snippet.textContent=String(item.snippet||item.description||(external?'Links to public resources at their original destinations.':'Candidate catalog preview.'));
    const counts=document.createElement('small');counts.className='catalog-counts';
    const recordCount=Number.isSafeInteger(item.record_count)?item.record_count:0;
    const pageCount=Number.isSafeInteger(item.source_page_count)?item.source_page_count:0;
    counts.textContent=external
      ?'External links only · excluded from page-derived record counts'
      :recordCount.toLocaleString()+' records · '+pageCount.toLocaleString()+' source pages';
    const matched=document.createElement('small');matched.className='catalog-matched';
    const matchedRecords=Array.isArray(item.matched_records)?item.matched_records.slice(0,3):[];
    matched.textContent=external
      ?'Open each destination at its original public source; no source-page content is republished here.'
      :matchedRecords.length
        ?'Matched records: '+matchedRecords.map(record=>String(record.record_type||'record')+': '+String(record.title||'Untitled')).join(' · ')
        :'Matched record titles are not available in this preview.';
    const rights=document.createElement('p');rights.className='catalog-rights';
    if(external){
      rights.textContent='External links only · destination terms and rights apply · no source-page records republished';
    }else{
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
    }
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
  makeHeading(q,'Web results',visibleCount.toLocaleString()+' web results · '+catalogCount.toLocaleString()+' catalog previews'+(indexedCount?' · '+indexedCount.toLocaleString()+' indexed pages':''));
  const list=document.createElement('div');list.className='results-list';
  for(const item of (Array.isArray(data.results)?data.results:[])){
    const card=document.createElement('article');card.className='eh-result';
    const link=document.createElement('a');link.href=safeUrl(item.url);link.target='_blank';link.rel='noopener noreferrer';link.textContent=item.title||item.url||'Web result';
    const snippet=document.createElement('p');snippet.textContent=item.snippet||item.description||'';
    const source=document.createElement('small');source.textContent=(item.source||'Public web')+(item.origin?' · '+item.origin:'');
    card.append(link,snippet,source);list.append(card);
  }
  if(!list.children.length&&!catalogCount){const empty=document.createElement('div');empty.className='results-empty';empty.textContent='No matching web results came back. Try another search or category.';list.append(empty);}
  resultsRoot.append(list);
  if(data.catalogPreview&&data.catalogPreview.status==='stale-candidate-preview'){
    const note=document.createElement('p');note.className='results-note';
    note.textContent='Catalog preview is based on an older source-index snapshot. The latest crawl has not yet been reanalyzed; these candidate matches are provisional.';
    resultsRoot.append(note);
  }else if(!catalogCount&&data.catalogPreview&&data.catalogPreview.status!=='ready'){
    const note=document.createElement('p');note.className='results-note';
    note.textContent='No matching candidate catalog records were found in the current preview. Candidate records remain provisional until source terms and record-level rights are reviewed.';
    resultsRoot.append(note);
  }
}
function renderImages(data,q){
  resultsRoot.replaceChildren();
  const items=Array.isArray(data.items)?data.items:[];
  makeHeading(q,"Image results",items.length.toLocaleString()+" licensed images · "+(data.provider||"public sources"));
  const grid=document.createElement("div");grid.className="media-grid";
  for(let index=0;index<items.length;index++){
    const item=items[index],card=document.createElement("article");card.className="media-card";
    card.dataset.license=String(item.license||"");card.dataset.creator=String(item.creator||"");
    const link=document.createElement("a");link.href=safeUrl(item.url);link.target="_blank";link.rel="noopener noreferrer";
    const img=document.createElement("img");img.src=safeUrl(item.thumbnail);img.alt=item.title||q;img.loading=index<8?"eager":"lazy";img.decoding="async";img.referrerPolicy="no-referrer";
    img.onerror=()=>{const placeholder=document.createElement("div");placeholder.className="image-placeholder";placeholder.textContent="Preview unavailable";img.replaceWith(placeholder);};
    link.append(img);
    const title=document.createElement("a");title.className="video-title";title.href=safeUrl(item.url);title.target="_blank";title.rel="noopener noreferrer";title.textContent=item.title||"Image";
    const small=document.createElement("small");small.append(document.createTextNode("Source: "+String(item.source||"Internet Archive")));
    if(item.creator){
      if(safeUrl(item.creatorUrl)!=="#"){const creatorLink=document.createElement("a");creatorLink.href=safeUrl(item.creatorUrl);creatorLink.target="_blank";creatorLink.rel="noopener noreferrer";creatorLink.textContent=item.creator;small.append(creatorLink);}
      else small.append(document.createTextNode(item.creator));
      small.append(document.createTextNode(" · Creator: "+item.creator));
    }
    small.append(document.createTextNode(" · "));
    const licenseLink=document.createElement("a");licenseLink.href=safeUrl(item.licenseUrl);licenseLink.target="_blank";licenseLink.rel="noopener noreferrer";licenseLink.textContent=item.license||"License";small.append(licenseLink);
    card.append(link,title,small);grid.append(card);
  }
  if(!items.length){const empty=document.createElement("div");empty.className="results-empty";empty.textContent="No images with verified license and attribution metadata were returned. Try another search.";resultsRoot.append(empty);return;}
  resultsRoot.append(grid);
}
function renderVideos(data,q){
  resultsRoot.replaceChildren();
  const items=Array.isArray(data.items)?data.items:[];
  makeHeading(q,"Video results",items.length.toLocaleString()+" videos · "+(data.provider||"Internet Archive"));
  const grid=document.createElement("div");grid.className="video-grid";
  for(const item of items){
    const card=document.createElement("article");card.className="video-card";card.dataset.license=String(item.license||"");card.dataset.creator=String(item.creator||item.credit||"");
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
  status.textContent="Searching licensed image collections…";
  let data=null,lastError=null;
  try{
    const u=new URL("https://everythinghub-search-backend.vercel.app/api/images");u.searchParams.set("q",q);
    const response=await fetch(u,{signal:AbortSignal.timeout(25000)}),candidate=await response.json().catch(()=>({}));
    if(!response.ok||candidate.status!=="ok"||!Array.isArray(candidate.items)||!candidate.items.length)throw new Error(candidate.message||"Image search unavailable");
    data=candidate;
  }catch(error){lastError=error;}
  if(!data){
    data=await requestSupabaseMediaSearch(q,"image");
    if(!data.items.length)throw new Error(lastError?.message||"No license-cleared image thumbnails are available for this search.");
  }
  renderImages(data,q);status.textContent=data.count.toLocaleString()+" licensed images from "+(data.provider||"public sources")+".";
}
async function runVideoSearch(q){
  status.textContent='Searching licensed public video sources…';
  let data=null,lastError=null;
  try{
    const u=new URL('https://everythinghub-search-backend.vercel.app/api/videos');u.searchParams.set('q',q);
    const response=await fetch(u,{signal:AbortSignal.timeout(25000)}),candidate=await response.json().catch(()=>({}));
    if(!response.ok||candidate.status!=='ok'||!Array.isArray(candidate.items)||!candidate.items.length)throw new Error(candidate.message||'Video search unavailable');
    data=candidate;
  }catch(error){lastError=error;}
  if(!data){
    data=await requestSupabaseMediaSearch(q,'video');
    if(!data.items.length)throw new Error(lastError?.message||'No license-cleared video thumbnails are available for this search.');
  }
  renderVideos(data,q);status.textContent=data.count.toLocaleString()+' licensed videos from '+(data.provider||'public sources')+'.';
}
async function requestIndexedSearch(q,lane){
  const u=new URL('https://everythinghub-search-backend.vercel.app/api/search');
  u.searchParams.set('q',q);
  u.searchParams.set('limit','20');
  if(lane)u.searchParams.set('lane',lane);
  const response=await fetch(u,{headers:{accept:'application/json'},signal:AbortSignal.timeout(30000)});
  if(!response.ok)throw new Error('Independent Everything Hub search '+response.status);
  return response.json();
}
function hasIndexedResults(data){
  return Boolean(data&&((Array.isArray(data.results)&&data.results.length)||(Array.isArray(data.catalogResults)&&data.catalogResults.length)));
}
async function runSearch(){
  const q=input.value.trim();if(!q){input.focus();return;}
  status.textContent='Searching…';resultsRoot.replaceChildren();
  const pageUrl=new URL(location.href);pageUrl.searchParams.set('q',q);pageUrl.searchParams.set('mode',mode);history.replaceState({},'',pageUrl);
  try{
    if(mode==='images'){await runImageSearch(q);return;}
    if(mode==='videos'){await runVideoSearch(q);return;}
    let data=null,lastError=null;
    try{
      const candidate=await requestIndexedSearch(q,mode);
      if(hasIndexedResults(candidate))data=candidate;
    }catch(error){lastError=error;}
    if(!data&&mode!=='news'){
      try{
        const candidate=await requestIndexedSearch(q,'');
        if(hasIndexedResults(candidate))data={...candidate,laneFallback:true,requestedLane:mode};
      }catch(error){if(!lastError)lastError=error;}
    }
    if(!data&&mode==='news'){
      try{
        const fast=new URL('https://everythinghub-search-backend.vercel.app/api/search-fast');
        fast.searchParams.set('q',q);fast.searchParams.set('lane','news');
        const response=await fetch(fast,{headers:{accept:'application/json'},signal:AbortSignal.timeout(12000)});
        if(!response.ok)throw new Error('News fallback returned HTTP '+response.status);
        const fastData=await response.json();
        if(fastData.status==='ok'&&Array.isArray(fastData.results)&&fastData.results.length)data=fastData;
        else throw new Error(fastData.message||'No robots-verified public news results returned');
      }catch(error){if(!lastError)lastError=error;}
    }
    if(data&&(!Array.isArray(data.results)||!data.results.length)&&Array.isArray(data.catalogResults)&&data.catalogResults.length&&mode==='news'){
      try{
        const fast=new URL('https://everythinghub-search-backend.vercel.app/api/search-fast');
        fast.searchParams.set('q',q);fast.searchParams.set('lane','news');
        const response=await fetch(fast,{headers:{accept:'application/json'},signal:AbortSignal.timeout(12000)});
        if(response.ok){
          const fastData=await response.json();
          if(fastData.status==='ok'&&Array.isArray(fastData.results)&&fastData.results.length){
            data={...data,results:fastData.results,count:fastData.count||fastData.results.length,totalMatches:fastData.totalMatches||fastData.count||fastData.results.length,providers:Array.from(new Set([...(data.providers||[]),...(fastData.providers||[])])),fallback:fastData.fallback||data.fallback};
          }
        }
      }catch(error){}
    }
    if(!data||!Array.isArray(data.results)||!data.results.length){
      try{
        const fallback=await requestSupabaseSearch(q,mode);
        if(fallback)data={...fallback,catalogResults:data?.catalogResults||fallback.catalogResults||[]};
      }catch(error){if(!lastError)lastError=error;}
    }
    if((!data||!Array.isArray(data.results)||!data.results.length)&&mode==='news'){
      try{
        const fallback=await requestSupabaseIndependentSearch(q);
        if(fallback.results.length)data={...fallback,catalogResults:data?.catalogResults||[]};
      }catch(error){if(!lastError)lastError=error;}
    }
    if(!data){
      throw lastError||new Error('No matching indexed results were found. External fallback is disabled for this category under the current robots policy.');
    }
    renderResults(data,q);
    const pageCount=Array.isArray(data.results)?data.results.length:(Number(data.count)||0);
    const catalogCount=Array.isArray(data.catalogResults)?data.catalogResults.length:0;
    const laneNote=data.laneFallback?' · related results from all indexed categories':data.independentOfDatabase?' · independent public-source fallback':'';
    status.textContent=pageCount.toLocaleString()+' web results and '+catalogCount.toLocaleString()+' catalog previews from '+(data.providers||[]).join(' and ')+laneNote+'.';
  }catch(error){
    status.textContent=mode==='images'?'Image search is temporarily unavailable.':mode==='videos'?'Video sources are temporarily unavailable.':'Search is temporarily unavailable. Please try again.';
    const message=document.createElement('div');message.className='results-empty';message.textContent=error.message||'The search service did not respond. Please try again.';resultsRoot.replaceChildren(message);
  }
}
const newsItems=document.getElementById('news-items'),newsStatus=document.getElementById('news-status');
let activeNewsCategory='for-you';
async function loadNews(category){
  activeNewsCategory=category;newsStatus.textContent='Loading the latest stories…';newsItems.replaceChildren();
  document.querySelectorAll('[data-news]').forEach(b=>b.classList.toggle('active',b.dataset.news===category));
  const queries={
    'for-you':'latest world news today',
    world:'latest world news today',
    technology:'latest technology news artificial intelligence',
    business:'latest business news economy markets',
    science:'latest science and space news'
  };
  try{
    const u=new URL('https://everythinghub-search-backend.vercel.app/api/news');u.searchParams.set('category',category);
    let data=null,lastError=null;
    for(let attempt=0;attempt<2;attempt++){
      try{
        const response=await fetch(u,{signal:AbortSignal.timeout(15000),cache:'no-store'});
        let payload={};
        try{payload=await response.json();}catch{}
        if(!response.ok){const error=new Error(payload.message||'News service '+response.status);error.status=response.status;throw error;}
        if(payload.status!=='ok'||!Array.isArray(payload.items)||!payload.items.length)throw new Error(payload.message||'No news stories returned');
        data=payload;break;
      }catch(error){
        lastError=error;
        const status=Number(error.status)||0;
        const transient=(status>=500&&status<=599)||error.name==='AbortError'||error.name==='TimeoutError'||/Failed to fetch|NetworkError|Load failed|timed out/i.test(error.message||'');
        if(!transient||attempt===1)break;
        newsStatus.textContent='News feed reconnecting…';
        await new Promise(resolve=>setTimeout(resolve,750));
      }
    }
    if(!data){
      const fallback=await requestSupabaseIndependentSearch(queries[category]||queries['for-you']);
      const items=fallback.results.filter(item=>item&&item.title&&safeUrl(item.url)!=='#').slice(0,20);
      if(!items.length)throw lastError||new Error('No public-source news stories returned');
      data={items,count:items.length,updatedAt:new Date().toISOString(),provider:fallback.provider||'Bing RSS'};
    }
    newsStatus.textContent='Updated '+new Date(data.updatedAt||Date.now()).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'})+' · '+data.count+' stories · '+(data.provider||'public sources');
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
