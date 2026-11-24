'use strict';
const https=require('node:https');
const zlib=require('node:zlib');
const crypto=require('node:crypto');
const {tokenize}=require('./search-core');

const RAW='https://raw.githubusercontent.com/rshure001/EverythingHub-Crawler/';
const REF='https://api.github.com/repos/rshure001/EverythingHub-Crawler/git/ref/heads/main';
const CACHE_MS=300000, STALE_MS=3600000;
const MAX_QUERY_CANDIDATES=12, MAX_CATALOG_INDEX_GZIP=8000000, MAX_CATALOG_INDEX_JSON=40000000;
let manifestCache=null; const queryCache=new Map();
const MAX_MANIFEST=8000000, MAX_REGISTRY_GZIP=5000000, MAX_RECORDS_GZIP=16000000;
const MAX_REGISTRY_JSON=32000000, MAX_RECORDS_JSON=96000000;
const CONTENT_INTENT=new Set(['article','articles','news','research','study','studies','report','reports','guide','guides','tutorial','documentation','paper','papers','dataset','datasets','video','videos','image','images','photo','photos','media','book','books']);
const ATTRIBUTE_KEYS=new Set(['brand','model','sku','mpn','gtin','manufacturer','category','address_locality','address_region','address_country','city','state','region','country','postal_code','location_name','area_served','service_type']);
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
 if(!Number.isSafeInteger(m.source_index_pages)||m.source_index_pages<1||m.source_index_pages>250000)throw new Error('page_catalog_source_count_invalid');
 if(!Number.isSafeInteger(m.catalog_count)||m.catalog_count<1||m.catalog_count>25000||!Number.isSafeInteger(m.record_count)||m.record_count<1||m.record_count>1200000)throw new Error('page_catalog_counts_invalid');
 if(typeof m.generated_at!=='string'||!Number.isFinite(Date.parse(m.generated_at))||!Array.isArray(m.files)||m.files.length<2||m.files.length>40000)throw new Error('page_catalog_manifest_invalid');
 const files=new Map();
 for(const f of m.files){
  if(!f||typeof f.file!=='string'||f.file.startsWith('/')||f.file.includes('\\')||f.file.split('/').some(p=>!p||p==='.'||p==='..')||!Number.isSafeInteger(f.bytes)||f.bytes<1||typeof f.sha256!=='string'||!/^[a-f0-9]{64}$/i.test(f.sha256))throw new Error('page_catalog_file_entry_invalid');
  if(files.has(f.file))throw new Error('page_catalog_duplicate_file');
  files.set(f.file,f);
 }
 if(!files.has('registry.json.gz'))throw new Error('page_catalog_artifacts_missing');
 const recordShards=Array.from(files.values()).filter(f=>f.file.startsWith('records/')).sort((a,b)=>a.file.localeCompare(b.file));
 if(files.has('records.json.gz')&&recordShards.length)throw new Error('page_catalog_mixed_record_artifacts');
 if(!files.has('records.json.gz')&&!recordShards.length)throw new Error('page_catalog_artifacts_missing');
 if(recordShards.length){
  if(recordShards.length>512||recordShards.some((f,index)=>!/^records\\/\\d{5}\\.json\\.gz$/.test(f.file)||f.format!=='json'||!Number.isSafeInteger(f.items)||f.items<0))throw new Error('page_catalog_record_shards_invalid');
  if(recordShards.reduce((sum,f)=>sum+f.items,0)!==m.record_count)throw new Error('page_catalog_record_shard_count_mismatch');
 }
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
 let payload;
 if(Buffer.isBuffer(recordsRaw)){
  payload=decodeArtifact(files.get('records.json.gz'),recordsRaw,MAX_RECORDS_GZIP,MAX_RECORDS_JSON,'page_catalog_records');
 }else if(Array.isArray(recordsRaw)){
  const entries=Array.from(files.values()).filter(f=>f.file.startsWith('records/')).sort((a,b)=>a.file.localeCompare(b.file));
  if(recordsRaw.length!==entries.length)throw new Error('page_catalog_record_shard_set_incomplete');
  const merged=[];
  for(let index=0;index<entries.length;index++){
   const entry=entries[index],part=recordsRaw.find(item=>item&&item.file===entry.file);
   if(!part||!Buffer.isBuffer(part.raw))throw new Error('page_catalog_record_shard_missing');
   const shard=decodeArtifact(entry,part.raw,MAX_RECORDS_GZIP,MAX_RECORDS_JSON,'page_catalog_records_shard');
   if(!shard||shard.version!==1||shard.shard!==index||shard.shard_count!==entries.length||!Array.isArray(shard.records)||shard.records.length!==entry.items)throw new Error('page_catalog_record_shard_schema_invalid');
   merged.push(...shard.records);
  }
  payload={version:1,records:merged};
 }else throw new Error('page_catalog_records_payload_invalid');
 if(!registry||registry.version!==1||registry.status!=='candidate_staging_not_production'||!Array.isArray(registry.catalogs)||registry.catalogs.length!==manifest.catalog_count)throw new Error('page_catalog_registry_schema_invalid');
 if(!payload||payload.version!==1||!Array.isArray(payload.records)||payload.records.length!==manifest.record_count)throw new Error('page_catalog_records_schema_invalid');
 const policyReview=registry.source_policy_review||manifest.source_policy_review||null;
 if(policyReview){
  if(manifest.source_policy_review&&registry.source_policy_review&&
   manifest.source_policy_review.register_sha256!==registry.source_policy_review.register_sha256)throw new Error('page_catalog_policy_review_digest_mismatch');
  if(policyReview.schema_version!==1||typeof policyReview.register_sha256!=='string'||
   !/^[a-f0-9]{64}$/i.test(policyReview.register_sha256)||policyReview.scope!=='source-level-only'||
   policyReview.record_level_clearance!=='not_reviewed'||policyReview.commercial_eligibility!==false||
   policyReview.republication!=='not_cleared')throw new Error('page_catalog_policy_review_invalid');
 }
 const validatePolicyAnnotation=item=>{
  if(!policyReview)return;
  const status=item.source_policy_review_status;
  const hosts=item.source_policy_review_hosts;
  const evidence=item.source_policy_review_evidence_urls;
  if(!['not_reviewed','conditional_source_review','partial_source_review'].includes(status)||
   item.record_level_clearance!=='not_reviewed'||item.source_terms_status!=='not_reviewed'||
   item.commercial_eligible!==false)throw new Error('page_catalog_record_policy_annotation_invalid');
  if(status!=='not_reviewed'&&(!Array.isArray(hosts)||!hosts.length||
   !Array.isArray(evidence)||!evidence.length||item.source_policy_review_scope!=='source-level-only'))throw new Error('page_catalog_record_policy_evidence_invalid');
  if(status==='not_reviewed'&&((Array.isArray(hosts)&&hosts.length)||(Array.isArray(evidence)&&evidence.length)))throw new Error('page_catalog_unreviewed_policy_has_evidence');
 };
 const catalogs=new Map();
 for(const c of registry.catalogs){
  if(!c||typeof c.catalog_id!=='string'||!c.catalog_id||catalogs.has(c.catalog_id)||!Array.isArray(c.record_ids)||c.record_count!==c.record_ids.length||!Array.isArray(c.source_page_ids)||c.source_page_count!==c.source_page_ids.length||c.commercial_eligible!==false)throw new Error('page_catalog_identity_or_membership_invalid');
  if(c.source_url&&!safeUrl(c.source_url))throw new Error('page_catalog_source_url_invalid');
  validatePolicyAnnotation(c);
  catalogs.set(c.catalog_id,c);
 }
 const records=new Map();
 for(const r of payload.records){
  if(!r||typeof r.record_id!=='string'||!r.record_id||records.has(r.record_id)||!Array.isArray(r.catalog_ids)||r.commercial_eligible!==false||!Array.isArray(r.source_urls)||!r.source_urls.length||r.source_urls.some(u=>!safeUrl(u)))throw new Error('page_record_identity_or_provenance_invalid');
  if(new Set(r.catalog_ids).size!==r.catalog_ids.length||r.catalog_ids.some(id=>!catalogs.has(id)))throw new Error('page_record_catalog_membership_invalid');
  validatePolicyAnnotation(r);
  records.set(r.record_id,r);
 }
 for(const c of registry.catalogs)if(new Set(c.record_ids).size!==c.record_ids.length||c.record_ids.some(id=>!records.has(id)))throw new Error('page_catalog_record_reference_invalid');
 return {catalogs:registry.catalogs,records:payload.records,generatedAt:manifest.generated_at,analysisVersion:manifest.analysis_version,sourceIndexPages:manifest.source_index_pages,sourceIndexUpdatedAt:manifest.source_index_updated_at,sourceIndexSha256:manifest.source_index_sha256,catalogCount:manifest.catalog_count,recordCount:manifest.record_count,metrics:manifest.metrics||{},sourcePolicyReview:policyReview};
}
async function loadPageDerivedCatalogsFromManifest(manifest,commitSha,manifestRaw,fetchBuffer=getBuffer){
 if(typeof commitSha!=='string'||!/^[a-f0-9]{40}$/i.test(commitSha))throw new Error('page_catalog_git_ref_invalid');
 if(!Buffer.isBuffer(manifestRaw))manifestRaw=Buffer.from(manifestRaw||'');
 if(!manifestRaw.length||manifestRaw.length>MAX_MANIFEST)throw new Error('page_catalog_manifest_raw_size_invalid');
 const files=validateManifest(manifest),base=RAW+commitSha+'/data/page-derived-catalogs/';
 const recordEntries=Array.from(files.values()).filter(f=>f.file.startsWith('records/')).sort((a,b)=>a.file.localeCompare(b.file));
 const recordsPromise=files.has('records.json.gz')
  ?fetchBuffer(base+encodeURIComponent('records.json.gz'),MAX_RECORDS_GZIP,9000)
  :(async()=>{
    const shards=[];
    for(let offset=0;offset<recordEntries.length;offset+=8){
     const batch=await Promise.all(recordEntries.slice(offset,offset+8).map(async entry=>({
      file:entry.file,
      raw:await fetchBuffer(base+entry.file.split('/').map(encodeURIComponent).join('/'),MAX_RECORDS_GZIP,9000)
     })));
     shards.push(...batch);
    }
    return shards;
   })();
 const [registryRaw,recordsRaw]=await Promise.all([
  fetchBuffer(base+encodeURIComponent('registry.json.gz'),MAX_REGISTRY_GZIP,9000),
  recordsPromise
 ]);
 return validatePageDerivedCatalogs(manifest,registryRaw,recordsRaw);
}
async function loadPageDerivedCatalogs(query){
 if(String(query||'').trim())return loadQueryScopedPageDerivedCatalogs(query);
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

async function loadQueryScopedPageDerivedCatalogs(query,fetchBuffer=getBuffer){
 const text=String(query||'').trim();
 if(!text||text.length>160)throw new Error('page_catalog_query_invalid');
 const terms=Array.from(new Set(tokenize(text))).slice(0,12);
 if(!terms.length)return {catalogs:[],records:[],generatedAt:null,analysisVersion:null,sourceIndexPages:0,catalogCount:0,recordCount:0,metrics:{},sourcePolicyReview:null};
 const cacheKey=text.toLowerCase();
 const prior=queryCache.get(cacheKey);
 if(prior&&Date.now()-prior.loadedAt<CACHE_MS)return {...prior,stale:false};
 try{
  let snapshot=manifestCache;
  if(!snapshot||Date.now()-snapshot.loadedAt>=CACHE_MS){
   const ref=JSON.parse((await fetchBuffer(REF+'?v='+Date.now(),4096,7000)).toString('utf8'));
   const sha=ref&&ref.object&&ref.object.sha;
   if(typeof sha!=='string'||!/^[a-f0-9]{40}$/i.test(sha))throw new Error('page_catalog_git_ref_invalid');
   const manifestRaw=await fetchBuffer(RAW+sha+'/data/page-derived-catalogs/manifest.json',MAX_MANIFEST,7000);
   let manifest;try{manifest=JSON.parse(manifestRaw.toString('utf8'));}catch{throw new Error('page_catalog_manifest_json_invalid');}
   const files=validateManifest(manifest);
   const policyReview=manifest.source_policy_review||null;
   if(policyReview&&(policyReview.schema_version!==1||typeof policyReview.register_sha256!=='string'||!/^[a-f0-9]{64}$/i.test(policyReview.register_sha256)||policyReview.scope!=='source-level-only'||policyReview.record_level_clearance!=='not_reviewed'||policyReview.commercial_eligibility!==false||policyReview.republication!=='not_cleared'))throw new Error('page_catalog_policy_review_invalid');
   snapshot={sha,manifest,files,policyReview,loadedAt:Date.now()};
   manifestCache=snapshot;
  }
  const {sha,manifest,files,policyReview}=snapshot;
  const base=RAW+sha+'/data/page-derived-catalogs/';
  const prefixes=Array.from(new Set(terms.map(term=>crypto.createHash('sha256').update(term).digest('hex').slice(0,2))));
  const shardEntries=prefixes.map(prefix=>files.get('search-index/'+prefix+'.json.gz')).filter(Boolean);
  if(!shardEntries.length)throw new Error('page_catalog_search_shard_missing');
  const shardPairs=await Promise.all(shardEntries.map(async entry=>{
   const raw=await fetchBuffer(base+entry.file.split('/').map(encodeURIComponent).join('/'),Math.min(2000000,entry.bytes),7000);
   const payload=decodeArtifact(entry,raw,2000000,20000000,'page_catalog_search_shard');
   const prefix=entry.file.split('/')[1].slice(0,2);
   if(!payload||payload.version!==1||payload.prefix!==prefix||!payload.tokens||typeof payload.tokens!=='object'||Array.isArray(payload.tokens))throw new Error('page_catalog_search_shard_schema_invalid');
   return [prefix,payload.tokens];
  }));
  const shardsByPrefix=new Map(shardPairs);
  let candidates=null;
  for(const term of terms){
   const prefix=crypto.createHash('sha256').update(term).digest('hex').slice(0,2);
   const postings=shardsByPrefix.get(prefix)&&shardsByPrefix.get(prefix)[term];
   const ids=new Set(Array.isArray(postings)?postings:[]);
   candidates=candidates===null?ids:new Set([...candidates].filter(id=>ids.has(id)));
   if(!candidates.size)break;
  }
  const ids=Array.from(candidates||[]).slice(0,MAX_QUERY_CANDIDATES);
  if(!ids.length){
   const empty={catalogs:[],records:[],generatedAt:manifest.generated_at,analysisVersion:manifest.analysis_version,sourceIndexPages:manifest.source_index_pages,sourceIndexUpdatedAt:manifest.source_index_updated_at,sourceIndexSha256:manifest.source_index_sha256,catalogCount:manifest.catalog_count,recordCount:manifest.record_count,metrics:manifest.metrics||{},sourcePolicyReview:policyReview,loadedAt:Date.now()};
   queryCache.set(cacheKey,empty);return {...empty};
  }
  const loaded=await Promise.allSettled(ids.map(async id=>{
   const digest=crypto.createHash('sha256').update(id).digest('hex');
   const path='indexes/'+digest.slice(0,2)+'/'+digest.slice(0,24)+'.json.gz';
   const entry=files.get(path);
   if(!entry||entry.bytes>MAX_CATALOG_INDEX_GZIP)throw new Error('page_catalog_index_missing_or_too_large');
   const raw=await fetchBuffer(base+path.split('/').map(encodeURIComponent).join('/'),MAX_CATALOG_INDEX_GZIP,8000);
   const payload=decodeArtifact(entry,raw,MAX_CATALOG_INDEX_GZIP,MAX_CATALOG_INDEX_JSON,'page_catalog_index');
   if(!payload||payload.version!==1||payload.catalog_id!==id||!Array.isArray(payload.records)||payload.records.length!==entry.items)throw new Error('page_catalog_index_schema_invalid');
   const seen=new Set();
   for(const record of payload.records){
    if(!record||typeof record.record_id!=='string'||!record.record_id||seen.has(record.record_id)||!Array.isArray(record.catalog_ids)||!record.catalog_ids.includes(id)||record.commercial_eligible!==false||!Array.isArray(record.source_urls)||!record.source_urls.length||record.source_urls.some(url=>!safeUrl(url)))throw new Error('page_catalog_index_record_invalid');
    seen.add(record.record_id);
    if(policyReview){
     const status=record.source_policy_review_status,hosts=record.source_policy_review_hosts,evidence=record.source_policy_review_evidence_urls;
     if(!['not_reviewed','conditional_source_review','partial_source_review'].includes(status)||record.record_level_clearance!=='not_reviewed'||record.source_terms_status!=='not_reviewed'||record.commercial_eligible!==false)throw new Error('page_catalog_record_policy_annotation_invalid');
     if(status!=='not_reviewed'&&(!Array.isArray(hosts)||!hosts.length||!Array.isArray(evidence)||!evidence.length||record.source_policy_review_scope!=='source-level-only'))throw new Error('page_catalog_record_policy_evidence_invalid');
     if(status==='not_reviewed'&&((Array.isArray(hosts)&&hosts.length)||(Array.isArray(evidence)&&evidence.length)))throw new Error('page_catalog_unreviewed_policy_has_evidence');
    }
   }
   const records=payload.records,counts=new Map();
   for(const record of records){const type=String(record.record_type||'subject').toLowerCase();counts.set(type,(counts.get(type)||0)+1);}
   const recordType=Array.from(counts).sort((a,b)=>b[1]-a[1])[0]?.[0]||'subject';
   const typeMap={product:'commerce',brand:'commerce',model:'commerce',place:'geographic',business:'business_directory',service:'service_directory',event:'events',organization:'organization_directory'};
   const catalogType=typeMap[recordType]||'knowledge_base';
   const sourceUrl=records.flatMap(r=>r.source_urls||[]).map(safeUrl).find(Boolean)||'';
   if(!sourceUrl)throw new Error('page_catalog_index_source_missing');
   const sourceHost=new URL(sourceUrl).hostname;
   const pageIds=new Set(records.flatMap(r=>Array.isArray(r.source_page_ids)?r.source_page_ids:[]));
   const hosts=Array.from(new Set(records.flatMap(r=>Array.isArray(r.source_policy_review_hosts)?r.source_policy_review_hosts:[])));
   const statuses=records.map(r=>r.source_policy_review_status||'not_reviewed');
   const policyStatus=statuses.includes('conditional_source_review')?'conditional_source_review':statuses.includes('partial_source_review')?'partial_source_review':'not_reviewed';
   return {catalog:{catalog_id:id,title:String(payload.title||'Catalog'),catalog_type:catalogType,record_type:recordType,subject:'',source_host:sourceHost,source_url:sourceUrl,source_page_ids:Array.from(pageIds),source_page_count:pageIds.size,record_ids:records.map(r=>r.record_id),record_count:records.length,is_source_declared_catalog:false,catalog_origin:'page_derived_index',evidence_strength:'unknown',catalog_status:payload.status||'candidate_needs_source_validation',status:payload.status||'candidate_needs_source_validation',source_terms_status:'not_reviewed',source_policy_review_status:policyStatus,source_policy_review_hosts:hosts,record_level_clearance:'not_reviewed',rights_status:'publicly_accessible_not_cleared_for_redistribution',commercial_eligible:false},records};
  }));
  const catalogs=[],records=[],seenRecords=new Set();
  for(const item of loaded){if(item.status!=='fulfilled')continue;catalogs.push(item.value.catalog);for(const record of item.value.records)if(!seenRecords.has(record.record_id)){seenRecords.add(record.record_id);records.push(record);}}
  const result={catalogs,records,generatedAt:manifest.generated_at,analysisVersion:manifest.analysis_version,sourceIndexPages:manifest.source_index_pages,sourceIndexUpdatedAt:manifest.source_index_updated_at,sourceIndexSha256:manifest.source_index_sha256,catalogCount:manifest.catalog_count,recordCount:manifest.record_count,metrics:manifest.metrics||{},sourcePolicyReview:policyReview,loadedAt:Date.now(),queryCandidateCount:ids.length,queryCatalogsLoaded:catalogs.length};
  queryCache.set(cacheKey,result);if(queryCache.size>100)queryCache.delete(queryCache.keys().next().value);
  return {...result,stale:false};
 }catch(error){
  const cached=queryCache.get(cacheKey);
  if(cached&&Date.now()-cached.loadedAt<STALE_MS)return {...cached,stale:true,refreshError:String(error&&error.message||error).slice(0,160)};
  throw error;
 }
}

function searchPageDerivedCatalogs(query,network,limit=5){
 const text=String(query||'').trim();
 if(!text||text.length>160||!network||!Array.isArray(network.catalogs)||!Array.isArray(network.records))return [];
 const terms=Array.from(new Set(tokenize(text))).slice(0,12);if(!terms.length)return [];
 const phrase=terms.join(' '),contentIntent=terms.some(t=>CONTENT_INTENT.has(t));
 const productTerms=new Set(['product','products','model','models','watch','watches','buy','retailer','retailers','price','prices','reference','references']);
 const businessTerms=new Set(['business','businesses','restaurant','restaurants','hotel','hotels','store','stores','shop','shops','venue','venues','dealer','dealers']);
 const eventTerms=new Set(['event','events','concert','concerts','festival','festivals','calendar','schedule','schedules','grand','prix']);
 const businessIntent=terms.some(t=>businessTerms.has(t));
 const eventIntent=terms.some(t=>eventTerms.has(t));
 const productTermIntent=terms.some(t=>productTerms.has(t));
 const recordTokens=record=>{
  const titleTokens=new Set(tokenize(String(record.title||'')));
  const descTokens=new Set(tokenize(String(record.description||'').slice(0,2000)));
  const attrs=record.attributes&&typeof record.attributes==='object'?record.attributes:{};
  const attrText=Object.entries(attrs).filter(([k,v])=>ATTRIBUTE_KEYS.has(String(k).toLowerCase())&&(typeof v==='string'||typeof v==='number')).map(([,v])=>String(v)).join(' ');
  const attrTokens=new Set(tokenize(attrText));
  return {titleTokens,descTokens,attrTokens,all:new Set([...titleTokens,...descTokens,...attrTokens])};
 };
 const queryMatchesRecord=record=>terms.every(t=>recordTokens(record).all.has(t));
 const globalProductMatch=network.records.some(r=>['product','brand','model'].includes(String(r.record_type||'').toLowerCase())&&queryMatchesRecord(r));
 const globalPlaceMatch=network.records.some(r=>{
  const type=String(r.record_type||'').toLowerCase();
  if(type==='place')return queryMatchesRecord(r);
  if(!['business','service','organization'].includes(type))return false;
  const attrs=r.attributes&&typeof r.attributes==='object'?r.attributes:{};
  const hasLocation=['address_locality','address_region','address_country','city','state','region','country','postal_code','location_name','area_served'].some(key=>typeof attrs[key]==='string'&&attrs[key].trim());
  return hasLocation&&queryMatchesRecord(r);
 });
 const geoIntent=terms.length>=2&&globalPlaceMatch&&!contentIntent&&!eventIntent&&!businessIntent;
 const productIntent=!contentIntent&&!businessIntent&&(productTermIntent||(globalProductMatch&&!geoIntent));
 const locationBusinessIntent=businessIntent&&!contentIntent;
 const byId=new Map(network.catalogs.map(c=>[c.catalog_id,c])),groups=new Map();
 for(const record of network.records){
  if(!record||!Array.isArray(record.catalog_ids)||!record.catalog_ids.length)continue;
  const type=String(record.record_type||'').toLowerCase(),title=String(record.title||'').toLowerCase();
  const {titleTokens,descTokens,attrTokens}=recordTokens(record);
  const titleMatch=terms.every(t=>titleTokens.has(t)),attrMatch=terms.every(t=>attrTokens.has(t));
  const contentRecord=['article','resource','media','subject'].includes(type);
  // A local offer or brand name can mention a city without making the query a
  // commerce search. In a location/business query, keep typed place and local
  // business records; only retain commerce records when the user signals
  // product intent explicitly (for example, "Las Vegas watches").
  if((geoIntent||locationBusinessIntent)&&!productTermIntent&&['product','brand','model'].includes(type))continue;
  for(const id of record.catalog_ids){
   const c=byId.get(id);if(!c)continue;
   const catalogTokens=new Set(tokenize([c.title,c.subject].join(' ')));
   const catalogMatch=terms.every(t=>catalogTokens.has(t));
   if(!contentIntent){
    if(contentRecord&&!catalogMatch)continue;
    if(!contentRecord&&!titleMatch&&!attrMatch&&!catalogMatch)continue;
   }
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
  const matchedRecords=matched.slice(0,5).map(({record:r})=>({
   record_id:r.record_id,shared_catalog_ids:Array.isArray(r.catalog_ids)?r.catalog_ids:[],title:String(r.title||'Untitled record').slice(0,240),
   record_type:String(r.record_type||'record'),
   source_url:(r.source_urls||[]).map(safeUrl).find(Boolean)||'',
   last_seen_at:r.last_seen_at||null,
   source_policy_review_status:r.source_policy_review_status||'not_reviewed',
   record_level_clearance:r.record_level_clearance||'not_reviewed'
  }));
  const url=matchedRecords.map(r=>r.source_url).find(Boolean)||safeUrl(c.source_url)||'';if(!url)continue;
  const types=new Set(matched.map(x=>String(x.record.record_type||'').toLowerCase()));
  const recordScores=matched.slice(0,5).map(x=>x.score);
  let score=Math.max(...recordScores)+Math.min(30,recordScores.reduce((sum,value)=>sum+value,0)-Math.max(...recordScores));
  const ct=new Set(tokenize([c.title,c.subject].join(' ')));
  const contentCatalog=['article','resource','media','subject'].includes(String(c.record_type||'').toLowerCase());
  const catalogMatch=terms.every(t=>ct.has(t));
  if(catalogMatch)score+=contentCatalog&&!contentIntent?25+5*terms.length:100+10*terms.length;
  if(c.is_source_declared_catalog)score+=8;if(c.evidence_strength==='high')score+=4;
  if(productIntent){
   if(['product','brand','model'].some(t=>types.has(t)))score+=180;
   else if(['article','resource','media','subject'].some(t=>types.has(t)))score-=100;
  }else if(geoIntent){
   if(c.catalog_type==='geographic'&&types.has('place'))score+=180;
   else if(c.catalog_type==='business_directory'&&types.has('business'))score+=145;
   else if(c.catalog_type==='service_directory'&&types.has('service'))score+=140;
   else if(c.catalog_type==='organization_directory'&&types.has('organization'))score+=110;
   else if(c.catalog_type==='events'&&types.has('event'))score+=60;
   else if(['article','resource','media','subject'].some(t=>types.has(t)))score-=100;
  }else if(locationBusinessIntent){
   if(c.catalog_type==='business_directory'&&types.has('business'))score+=180;
   else if(c.catalog_type==='service_directory'&&types.has('service'))score+=160;
   else if(c.catalog_type==='geographic'&&types.has('place'))score+=100;
   else if(['article','resource','media','subject'].some(t=>types.has(t)))score-=80;
  }else if(eventIntent){
   if(c.catalog_type==='events'&&types.has('event'))score+=180;
   else if(['article','resource','media','subject'].some(t=>types.has(t)))score-=80;
  }else{
   if(c.catalog_type==='geographic')score+=types.has('place')&&terms.length>=2?70:-20;
   if(c.catalog_type==='commerce')score+=['product','brand','model'].some(t=>types.has(t))?50:-20;
   if(c.catalog_type==='business_directory')score+=types.has('business')?40:-15;
   if(c.catalog_type==='service_directory')score+=types.has('service')?40:-15;
   if(c.catalog_type==='organization_directory')score+=types.has('organization')?60:-15;
   if(c.catalog_type==='people_directory')score+=types.has('person')?60:-15;
  }
  let relevanceClass='record_match';
  if(productIntent&&['product','brand','model'].some(t=>types.has(t)))relevanceClass='typed_product_match';
  else if(geoIntent&&['place','business','service','organization','dataset'].some(t=>types.has(t)))relevanceClass='typed_location_match';
  else if(geoIntent&&types.has('event'))relevanceClass='related_event_match';
  else if(locationBusinessIntent&&['business','service'].some(t=>types.has(t)))relevanceClass='typed_business_match';
  else if(eventIntent&&types.has('event'))relevanceClass='typed_event_match';
  else if(types.has('organization'))relevanceClass='typed_organization_match';
  else if(['article','resource','media','subject'].some(t=>types.has(t)))relevanceClass='related_mention';
  else if(catalogMatch)relevanceClass='catalog_match';
  if(types.has('event')&&!eventIntent&&!['place','business','service','organization'].some(t=>types.has(t)))continue;
  const relevanceLabel={
   typed_product_match:'Product or model match',
   typed_location_match:'Place or local directory match',
   typed_business_match:'Business or service match',
   typed_event_match:'Event catalog match',
   typed_organization_match:'Organization match',
   related_event_match:'Related event mention for this location',
   related_mention:'Related mention, not a dedicated catalog match',
   catalog_match:'Catalog title match',
   record_match:'Record match'
  }[relevanceClass];
  let label=String(c.catalog_type||'subject').replace(/_/g,' ')+' catalog';
  if(c.catalog_type==='geographic')label='Place catalog';
  if(c.catalog_type==='commerce')label='Product catalog';
  if(c.catalog_type==='business_directory')label='Business catalog';
  if(c.catalog_type==='service_directory')label='Service catalog';
  const recordCount=Number.isSafeInteger(c.record_count)?c.record_count:matched.length;
  const sourcePageCount=Number.isSafeInteger(c.source_page_count)?c.source_page_count:0;
  const names=matchedRecords.map(r=>r.record_type+': '+r.title);
  const policyStatus=String(c.source_policy_review_status||'not_reviewed');
  const policyHosts=Array.isArray(c.source_policy_review_hosts)?c.source_policy_review_hosts:[];
  const policyMessage=policyStatus==='conditional_source_review'
   ?'Source-level policy review is conditional for '+policyHosts.join(', ')+'. Individual record rights remain unreviewed.'
   :policyStatus==='partial_source_review'
    ?'Source-level policy review is partial. Individual record rights remain unreviewed.'
    :'Source-level policy review is not recorded. Individual record rights remain unreviewed.';
  const snippet='Candidate catalog preview · '+recordCount.toLocaleString()+' records · '+sourcePageCount.toLocaleString()+' source pages. Matching records: '+names.join(' · ')+'. '+policyMessage+' Preview only.';
  const queryIntent=productIntent?'product':geoIntent?'location':locationBusinessIntent?'business':eventIntent?'event':contentIntent?'content':'general';
  if(relevanceClass==='related_mention')label+=' · Related mention';
  results.push({
   title:String(c.title||'Catalog')+' — '+label+': '+matchedRecords[0].title,
   url,snippet,description:snippet,
   source:'Everything Hub Catalog Preview · '+label,
   origin:'Everything Hub Catalog Preview',provider:'everythinghub-catalog-preview',
   mediaType:'web',score,catalog_id:id,catalog_title:String(c.title||''),
   catalog_type:c.catalog_type,record_type:c.record_type,
   catalog_status:c.catalog_status||c.status||'candidate_needs_source_validation',
   catalog_origin:c.catalog_origin||'unknown',evidence_strength:c.evidence_strength||'unknown',
   source_terms_status:c.source_terms_status||'not_reviewed',
   source_policy_review_status:policyStatus,source_policy_review_hosts:policyHosts,
   record_level_clearance:c.record_level_clearance||'not_reviewed',
   rights_status:c.rights_status||'publicly_accessible_not_cleared_for_redistribution',
   commercial_eligible:false,preview_only:true,query_intent:queryIntent,
   relevance_class:relevanceClass,relevance_label:relevanceLabel,
   record_count:recordCount,source_page_count:sourcePageCount,matched_records:matchedRecords
  });
 }
 const n=Number.isInteger(limit)?Math.max(1,Math.min(8,limit)):5;
 return results.sort((a,b)=>b.score-a.score||b.matched_records.length-a.matched_records.length||a.title.localeCompare(b.title)).slice(0,n);
}
function resolveRelatedRecords(recordId,network,limit=20,offset=0){
 const key=String(recordId||'').trim();
 if(!key||key.length>160)throw new Error('record_id_invalid');
 const records=Array.isArray(network?.records)?network.records:[];
 const owner=records.find(r=>r&&r.record_id===key);
 if(!owner)return null;
 const catalogs=Array.isArray(network?.catalogs)?network.catalogs:[];
 const catalogsById=new Map(catalogs.map(c=>[c.catalog_id,c]));
 const recordsById=new Map(records.map(r=>[r.record_id,r]));
 const related=new Map();
 for(const catalogId of Array.isArray(owner.catalog_ids)?owner.catalog_ids:[]){
  const catalog=catalogsById.get(catalogId);
  if(!catalog)continue;
  for(const relatedId of Array.isArray(catalog.record_ids)?catalog.record_ids:[]){
   if(relatedId===owner.record_id||related.has(relatedId))continue;
   const record=recordsById.get(relatedId);
   if(record)related.set(relatedId,record);
  }
 }
 const ordered=Array.from(related.values()).sort((a,b)=>String(a.title||'').localeCompare(String(b.title||''))||String(a.record_id).localeCompare(String(b.record_id)));
 const start=Math.max(0,Math.min(5000,Number.isInteger(offset)?offset:0));
 const size=Math.max(1,Math.min(50,Number.isInteger(limit)?limit:20));
 return {
  record_id:key,
  owner_record:{record_id:owner.record_id,title:owner.title,record_type:owner.record_type,description:owner.description||'',source_urls:owner.source_urls||[],source_page_ids:owner.source_page_ids||[],catalog_ids:owner.catalog_ids||[]},
  related_count:ordered.length,
  offset:start,
  limit:size,
  has_more:start+size<ordered.length,
  related_records:ordered.slice(start,start+size).map(record=>({record_id:record.record_id,title:record.title,record_type:record.record_type,description:record.description||'',source_urls:record.source_urls||[],source_page_ids:record.source_page_ids||[],catalog_ids:record.catalog_ids||[],last_seen_at:record.last_seen_at||null,source_terms_status:record.source_terms_status||'not_reviewed',record_level_clearance:record.record_level_clearance||'not_reviewed',commercial_eligible:false}))
 };
}
function matchesPageIndexSnapshot(pageDerived,pageIndex){
 const sourceTime=Date.parse(String(pageDerived&&pageDerived.sourceIndexUpdatedAt||''));
 const pageTime=Number(pageIndex&&pageIndex.updatedAt)*1000;
 return Number.isSafeInteger(pageDerived&&pageDerived.sourceIndexPages)&&
  pageDerived.sourceIndexPages===pageIndex.indexedPages&&Number.isFinite(sourceTime)&&
  Number.isFinite(pageTime)&&Math.abs(sourceTime-pageTime)<=60000;
}
function clearPageDerivedCatalogsCache(){cache=null;manifestCache=null;queryCache.clear();}
module.exports={validateManifest,validatePageDerivedCatalogs,loadPageDerivedCatalogsFromManifest,loadPageDerivedCatalogs,loadQueryScopedPageDerivedCatalogs,searchPageDerivedCatalogs,resolveRelatedRecords,matchesPageIndexSnapshot,clearPageDerivedCatalogsCache};
