'use strict';
const https=require('node:https');
const zlib=require('node:zlib');
const crypto=require('node:crypto');
const {tokenize}=require('./search-core');

const RAW='https://raw.githubusercontent.com/rshure001/EverythingHub-Crawler/';
const REF='https://api.github.com/repos/rshure001/EverythingHub-Crawler/git/ref/heads/main';
const CACHE_MS=300000, STALE_MS=3600000;
const MAX_MANIFEST=4000000, MAX_REGISTRY_GZIP=5000000, MAX_RECORDS_GZIP=16000000;
const MAX_REGISTRY_JSON=32000000, MAX_RECORDS_JSON=96000000;
const CONTENT_INTENT=new Set(['article','articles','news','research','study','studies','report','reports','guide','guides','tutorial','documentation','paper','papers','dataset','datasets','video','videos','image','images','photo','photos','media','book','books']);
const ATTRIBUTE_KEYS=new Set(['brand','model','sku','mpn','gtin','manufacturer','category']);
const agent=new https.Agent({keepAlive:true,maxSockets:4});
let cache=null;

function getBuffer(url,maxBytes,timeout=9000){
 return new Promise((resolve,reject)=>{
  let settled=false,request,total=0;const chunks=[];
  const fail=e=>{if(!settled){settled=true;reject(e);}};
  try{
   request=https.get(url,{agent,headers:{'user-agent':'EverythingHub-CatalogPreview/1.0',accept:'*/*'}},response=>{
    if(response.statusCode<200||response.statusCode>=300){response.resume();fail(new Error('catalog_preview_http_'+response.statusCode));return;}
    if(Number(response.headers['content-length']||0)>maxBytes){response.resume();fail(new Error('catalog_preview_size_limit'));return;}
    response.on('data',chunk=>{total+=chunk.length;if(total>maxBytes){fail(new Error('catalog_preview_size_limit'));request.destroy();return;}chunks.push(chunk);});
    response.on('end',()=>{if(!settled){settled=true;resolve(Buffer.concat(chunks,total));}});
    response.on('error',fail);
   });
   request.setTimeout(timeout,()=>request.destroy(new Error('catalog_preview_timeout')));
   request.on('error',fail);
  }catch(e){fail(e);}
 });
}
function safeUrl(value){
 try{const u=new URL(String(value||''));return ['http:','https:'].includes(u.protocol)&&!u.username&&!u.password?u.href:'';}catch{return '';}
}
function validateManifest(m){
 if(!m||m.version!==1||m.integrity!=='sha256')throw new Error('page_catalog_manifest_schema_invalid');
 if(!Number.isSafeInteger(m.source_index_pages)||m.source_index_pages<1||m.source_index_pages>150000)throw new Error('page_catalog_source_count_invalid');
 if(!Number.isSafeInteger(m.catalog_count)||m.catalog_count<1||m.catalog_count>10000||!Number.isSafeInteger(m.record_count)||m.record_count<1||m.record_count>250000)throw new Error('page_catalog_counts_invalid');
 if(typeof m.generated_at!=='string'||!Number.isFinite(Date.parse(m.generated_at))||!Array.isArray(m.files)||m.files.length<2||m.files.length>20000)throw new Error('page_catalog_manifest_invalid');
 const files=new Map();
 for(const f of m.files){
  if(!f||typeof f.file!=='string'||f.file.startsWith('/')||f.file.includes('\\')||f.file.split('/').some(p=>!p||p==='.'||p==='..')||!Number.isSafeInteger(f.bytes)||f.bytes<1||typeof f.sha256!=='string'||!/^[a-f0-9]{64}$/i.test(f.sha256))throw new Error('page_catalog_file_entry_invalid');
  if(files.has(f.file))throw new Error('page_catalog_duplicate_file');
  files.set(f.file,f);
 }
 if(!files.has('registry.json.gz')||!files.has('records.json.gz'))throw new Error('page_catalog_artifacts_missing');
 if(files.get('registry.json.gz').bytes>MAX_REGISTRY_GZIP||files.get('records.json.gz').bytes>MAX_RECORDS_GZIP)throw new Error('page_catalog_compressed_limit');
 return files;
}
function decodeArtifact(meta,raw,maxCompressed,maxDecoded,label){
 if(!Buffer.isBuffer(raw)||raw.length!==meta.bytes||raw.length>maxCompressed)throw new Error(label+'_size_mismatch');
 if(crypto.createHash('sha256').update(raw).digest('hex').toLowerCase()!==String(meta.sha256).toLowerCase())throw new Error(label+'_sha256_mismatch');
 let decoded;try{decoded=zlib.gunzipSync(raw,{maxOutputLength:maxDecoded});}catch{throw new Error(label+'_gzip_invalid_or_too_large');}
 try{return JSON.parse(decoded.toString('utf8'));}catch{throw new Error(label+'_json_invalid');}
}
function validatePageDerivedCatalogs(manifest,registryRaw,recordsRaw){
 const files=validateManifest(manifest);
 const registry=decodeArtifact(files.get('registry.json.gz'),registryRaw,MAX_REGISTRY_GZIP,MAX_REGISTRY_JSON,'page_catalog_registry');
 const payload=decodeArtifact(files.get('records.json.gz'),recordsRaw,MAX_RECORDS_GZIP,MAX_RECORDS_JSON,'page_catalog_records');
 if(!registry||registry.version!==1||registry.status!=='candidate_staging_not_production'||!Array.isArray(registry.catalogs)||registry.catalogs.length!==manifest.catalog_count)throw new Error('page_catalog_registry_schema_invalid');
 if(!payload||payload.version!==1||!Array.isArray(payload.records)||payload.records.length!==manifest.record_count)throw new Error('page_catalog_records_schema_invalid');
 const catalogs=new Map();
 for(const c of registry.catalogs){
  if(!c||typeof c.catalog_id!=='string'||!c.catalog_id||catalogs.has(c.catalog_id)||!Array.isArray(c.record_ids)||c.record_count!==c.record_ids.length||!Array.isArray(c.source_page_ids)||c.source_page_count!==c.source_page_ids.length||c.commercial_eligible!==false)throw new Error('page_catalog_identity_or_membership_invalid');
  if(c.source_url&&!safeUrl(c.source_url))throw new Error('page_catalog_source_url_invalid');
  catalogs.set(c.catalog_id,c);
 }
 const records=new Map();
 for(const r of payload.records){
  if(!r||typeof r.record_id!=='string'||!r.record_id||records.has(r.record_id)||!Array.isArray(r.catalog_ids)||r.commercial_eligible!==false||!Array.isArray(r.source_urls)||!r.source_urls.length||r.source_urls.some(u=>!safeUrl(u)))throw new Error('page_record_identity_or_provenance_invalid');
  if(new Set(r.catalog_ids).size!==r.catalog_ids.length||r.catalog_ids.some(id=>!catalogs.has(id)))throw new Error('page_record_catalog_membership_invalid');
  records.set(r.record_id,r);
 }
 for(const c of registry.catalogs)if(new Set(c.record_ids).size!==c.record_ids.length||c.record_ids.some(id=>!records.has(id)))throw new Error('page_catalog_record_reference_invalid');
 return {catalogs:registry.catalogs,records:payload.records,generatedAt:manifest.generated_at,analysisVersion:manifest.analysis_version,sourceIndexPages:manifest.source_index_pages,sourceIndexUpdatedAt:manifest.source_index_updated_at,sourceIndexSha256:manifest.source_index_sha256,catalogCount:manifest.catalog_count,recordCount:manifest.record_count,metrics:manifest.metrics||{}};
}
async function loadPageDerivedCatalogsFromManifest(manifest,commitSha,manifestRaw,fetchBuffer=getBuffer){
 if(typeof commitSha!=='string'||!/^[a-f0-9]{40}$/i.test(commitSha))throw new Error('page_catalog_git_ref_invalid');
 if(!Buffer.isBuffer(manifestRaw))manifestRaw=Buffer.from(manifestRaw||'');
 if(!manifestRaw.length||manifestRaw.length>MAX_MANIFEST)throw new Error('page_catalog_manifest_raw_size_invalid');
 const files=validateManifest(manifest),base=RAW+commitSha+'/data/page-derived-catalogs/';
 const [registryRaw,recordsRaw]=await Promise.all([
  fetchBuffer(base+encodeURIComponent('registry.json.gz'),MAX_REGISTRY_GZIP,9000),
  fetchBuffer(base+encodeURIComponent('records.json.gz'),MAX_RECORDS_GZIP,9000)
 ]);
 return validatePageDerivedCatalogs(manifest,registryRaw,recordsRaw);
}
async function loadPageDerivedCatalogs(){
 if(cache&&Date.now()-cache.loadedAt<CACHE_MS)return {...cache,stale:false};
 try{
  const ref=JSON.parse((await getBuffer(REF+'?v='+Date.now(),4096,7000)).toString('utf8'));
  const sha=ref&&ref.object&&ref.object.sha;
  if(typeof sha!=='string'||!/^[a-f0-9]{40}$/i.test(sha))throw new Error('page_catalog_git_ref_invalid');
  const manifestRaw=await getBuffer(RAW+sha+'/data/page-derived-catalogs/manifest.json',MAX_MANIFEST,7000);
  let manifest;try{manifest=JSON.parse(manifestRaw.toString('utf8'));}catch{throw new Error('page_catalog_manifest_json_invalid');}
  const loaded=await loadPageDerivedCatalogsFromManifest(manifest,sha,manifestRaw,getBuffer);
  cache={...loaded,loadedAt:Date.now(),stale:false};return {...cache};
 }catch(error){
  if(cache&&Date.now()-cache.loadedAt<STALE_MS)return {...cache,stale:true,refreshError:String(error&&error.message||error).slice(0,160)};
  throw error;
 }
}
function searchPageDerivedCatalogs(query,network,limit=5){
 const text=String(query||'').trim();
 if(!text||text.length>160||!network||!Array.isArray(network.catalogs)||!Array.isArray(network.records))return [];
 const terms=Array.from(new Set(tokenize(text))).slice(0,12);if(!terms.length)return [];
 const phrase=terms.join(' '),contentIntent=terms.some(t=>CONTENT_INTENT.has(t));
 const byId=new Map(network.catalogs.map(c=>[c.catalog_id,c])),groups=new Map();
 for(const record of network.records){
  if(!record||!Array.isArray(record.catalog_ids)||!record.catalog_ids.length)continue;
  const type=String(record.record_type||'').toLowerCase(),title=String(record.title||'').toLowerCase();
  const titleTokens=new Set(tokenize(title)),descTokens=new Set(tokenize(String(record.description||'').slice(0,2000)));
  const attrs=record.attributes&&typeof record.attributes==='object'?record.attributes:{};
  const attrText=Object.entries(attrs).filter(([k,v])=>ATTRIBUTE_KEYS.has(String(k).toLowerCase())&&(typeof v==='string'||typeof v==='number')).map(([,v])=>String(v)).join(' ');
  const attrTokens=new Set(tokenize(attrText)),titleMatch=terms.every(t=>titleTokens.has(t)),attrMatch=terms.every(t=>attrTokens.has(t));
  const contentRecord=['article','resource','media','subject'].includes(type);
  if(contentRecord&&!contentIntent&&!titleMatch)continue;
  for(const id of record.catalog_ids){
   const c=byId.get(id);if(!c)continue;
   const catalogTokens=new Set(tokenize([c.title,c.subject,c.source_host].join(' ')));
   const catalogMatch=terms.every(t=>catalogTokens.has(t));
   const searchable=new Set([...titleTokens,...attrTokens,...catalogTokens]);
   if(!contentRecord||contentIntent)for(const t of descTokens)searchable.add(t);
   if(!terms.every(t=>searchable.has(t)))continue;
   let score=20+(titleMatch?40:0)+(title.includes(phrase)?10:0);
   score+=terms.filter(t=>descTokens.has(t)).length*2+terms.filter(t=>attrTokens.has(t)).length*4;
   if(['product','brand','model'].includes(type)&&(titleMatch||attrMatch||catalogMatch))score+=24;
   if(type==='place'&&terms.length>=2)score+=18;
   if(catalogMatch)score+=contentRecord&&!contentIntent?5:15;
   if(!groups.has(id))groups.set(id,{catalog:c,matches:new Map()});
   const prior=groups.get(id).matches.get(record.record_id);
   if(!prior||score>prior.score)groups.get(id).matches.set(record.record_id,{record,score});
  }
 }
 const results=[];
 for(const [id,g] of groups){
  const c=g.catalog,matched=Array.from(g.matches.values()).sort((a,b)=>b.score-a.score||String(a.record.title||'').localeCompare(String(b.record.title||'')));
  if(!matched.length)continue;
  const matchedRecords=matched.slice(0,5).map(({record:r})=>({record_id:r.record_id,title:String(r.title||'Untitled record').slice(0,240),record_type:String(r.record_type||'record'),source_url:(r.source_urls||[]).map(safeUrl).find(Boolean)||'',last_seen_at:r.last_seen_at||null}));
  const url=safeUrl(c.source_url)||matchedRecords.map(r=>r.source_url).find(Boolean)||'';if(!url)continue;
  const types=new Set(matched.map(x=>String(x.record.record_type||'').toLowerCase()));
  let score=matched.slice(0,3).reduce((sum,x)=>sum+x.score,0);
  const ct=new Set(tokenize([c.title,c.subject,c.source_host].join(' ')));
  const contentCatalog=['article','resource','media','subject'].includes(String(c.record_type||'').toLowerCase());
  if(terms.every(t=>ct.has(t)))score+=contentCatalog&&!contentIntent?25+5*terms.length:100+10*terms.length;
  if(c.is_source_declared_catalog)score+=8;if(c.evidence_strength==='high')score+=4;
  if(c.catalog_type==='geographic')score+=types.has('place')&&terms.length>=2?70:-20;
  if(c.catalog_type==='commerce')score+=['product','brand','model'].some(t=>types.has(t))?50:-20;
  if(c.catalog_type==='business_directory')score+=types.has('business')?40:-15;
  if(c.catalog_type==='service_directory')score+=types.has('service')?40:-15;
  let label=String(c.catalog_type||'subject').replace(/_/g,' ')+' catalog';
  if(c.catalog_type==='geographic')label='Place catalog';
  if(c.catalog_type==='commerce')label='Product catalog';
  if(c.catalog_type==='business_directory')label='Business catalog';
  if(c.catalog_type==='service_directory')label='Service catalog';
  const recordCount=Number.isSafeInteger(c.record_count)?c.record_count:matched.length;
  const sourcePageCount=Number.isSafeInteger(c.source_page_count)?c.source_page_count:0;
  const names=matchedRecords.map(r=>r.record_type+': '+r.title);
  const snippet='Candidate catalog preview · '+recordCount.toLocaleString()+' records · '+sourcePageCount.toLocaleString()+' source pages. Matching records: '+names.join(' · ')+'. Source terms and reuse rights have not been reviewed; preview only.';
  results.push({title:String(c.title||'Catalog')+' — '+label+': '+matchedRecords[0].title,url,snippet,description:snippet,source:'Everything Hub Catalog Preview · '+label,origin:'Everything Hub Catalog Preview',provider:'everythinghub-catalog-preview',mediaType:'web',score,catalog_id:id,catalog_title:String(c.title||''),catalog_type:c.catalog_type,record_type:c.record_type,catalog_status:c.catalog_status||c.status||'candidate_needs_source_validation',catalog_origin:c.catalog_origin||'unknown',evidence_strength:c.evidence_strength||'unknown',source_terms_status:c.source_terms_status||'not_reviewed',rights_status:c.rights_status||'publicly_accessible_not_cleared_for_redistribution',commercial_eligible:false,preview_only:true,record_count:recordCount,source_page_count:sourcePageCount,matched_records:matchedRecords});
 }
 const n=Number.isInteger(limit)?Math.max(1,Math.min(8,limit)):5;
 return results.sort((a,b)=>b.score-a.score||b.matched_records.length-a.matched_records.length||a.title.localeCompare(b.title)).slice(0,n);
}
function matchesPageIndexSnapshot(pageDerived,pageIndex){
 const sourceTime=Date.parse(String(pageDerived&&pageDerived.sourceIndexUpdatedAt||''));
 const pageTime=Number(pageIndex&&pageIndex.updatedAt)*1000;
 return Number.isSafeInteger(pageDerived&&pageDerived.sourceIndexPages)&&
  pageDerived.sourceIndexPages===pageIndex.indexedPages&&Number.isFinite(sourceTime)&&
  Number.isFinite(pageTime)&&Math.abs(sourceTime-pageTime)<=60000;
}
function clearPageDerivedCatalogsCache(){cache=null;}
module.exports={validateManifest,validatePageDerivedCatalogs,loadPageDerivedCatalogsFromManifest,loadPageDerivedCatalogs,searchPageDerivedCatalogs,matchesPageIndexSnapshot,clearPageDerivedCatalogsCache};
