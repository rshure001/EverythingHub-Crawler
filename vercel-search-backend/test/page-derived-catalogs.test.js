'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
const zlib=require('node:zlib');
const {validatePageDerivedCatalogs,loadPageDerivedCatalogsFromManifest,loadQueryScopedPageDerivedCatalogs,searchPageDerivedCatalogs,resolveRelatedRecords}=require('../src/lib/page-derived-catalogs');
const gzip=value=>zlib.gzipSync(Buffer.from(JSON.stringify(value)));
const hash=buffer=>crypto.createHash('sha256').update(buffer).digest('hex');

function fixture(){
 const catalogs=[
  {catalog_id:'catalog-rolex',title:'Rolex Watches',catalog_type:'commerce',record_type:'product',subject:'watches',source_host:'example.com',source_url:'https://example.com/rolex',source_page_ids:['page-rolex'],source_page_count:1,record_ids:['record-rolex'],record_count:1,is_source_declared_catalog:true,catalog_origin:'source_declared_collection',evidence_strength:'high',catalog_status:'candidate_needs_source_validation',source_terms_status:'not_reviewed',rights_status:'publicly_accessible_not_cleared_for_redistribution',commercial_eligible:false,status:'candidate_needs_source_validation'},
  {catalog_id:'catalog-vegas',title:'Nevada Places',catalog_type:'geographic',record_type:'place',subject:'places',source_host:'example.net',source_url:'https://example.net/nevada',source_page_ids:['page-vegas'],source_page_count:1,record_ids:['record-vegas'],record_count:1,is_source_declared_catalog:true,catalog_origin:'source_declared_collection',evidence_strength:'high',catalog_status:'candidate_needs_source_validation',source_terms_status:'not_reviewed',rights_status:'publicly_accessible_not_cleared_for_redistribution',commercial_eligible:false,status:'candidate_needs_source_validation'},
  {catalog_id:'catalog-articles',title:'General Articles',catalog_type:'knowledge_base',record_type:'article',subject:'articles',source_host:'example.org',source_url:'https://example.org/articles',source_page_ids:['page-article'],source_page_count:1,record_ids:['record-article'],record_count:1,is_source_declared_catalog:true,catalog_origin:'source_declared_collection',evidence_strength:'high',catalog_status:'candidate_needs_source_validation',source_terms_status:'not_reviewed',rights_status:'publicly_accessible_not_cleared_for_redistribution',commercial_eligible:false,status:'candidate_needs_source_validation'}
 ];
 const records=[
  {record_id:'record-rolex',entity_id:'entity-rolex',canonical_entity_key:'product:rolex-submariner',record_type:'product',title:'Rolex Submariner Date',description:'Swiss dive watch',source_urls:['https://example.com/rolex/submariner'],source_page_ids:['page-rolex'],catalog_ids:['catalog-rolex'],last_seen_at:'2026-11-20T20:00:00Z',attributes:{brand:'Rolex',model:'Submariner Date'},commercial_eligible:false,source_terms_status:'not_reviewed',rights_status:'publicly_accessible_not_cleared_for_redistribution'},
  {record_id:'record-vegas',entity_id:'entity-vegas',canonical_entity_key:'place:las-vegas-strip',record_type:'place',title:'Las Vegas Strip',description:'Las Vegas, Nevada',source_urls:['https://example.net/places/las-vegas-strip'],source_page_ids:['page-vegas'],catalog_ids:['catalog-vegas'],last_seen_at:'2026-11-20T20:00:00Z',attributes:{},commercial_eligible:false,source_terms_status:'not_reviewed',rights_status:'publicly_accessible_not_cleared_for_redistribution'},
  {record_id:'record-article',entity_id:'entity-article',canonical_entity_key:'article:general-reference',record_type:'article',title:'General reference article',description:'Mentions Rolex once in passing',source_urls:['https://example.org/articles/general-reference'],source_page_ids:['page-article'],catalog_ids:['catalog-articles'],last_seen_at:'2026-11-20T20:00:00Z',attributes:{},commercial_eligible:false,source_terms_status:'not_reviewed',rights_status:'publicly_accessible_not_cleared_for_redistribution'}
 ];
 const registry=gzip({version:1,status:'candidate_staging_not_production',catalogs});
 const recordsFile=gzip({version:1,records});
 const manifest={version:1,integrity:'sha256',analysis_version:9,generated_at:'2026-11-20T22:21:02Z',source_index_pages:131899,source_index_updated_at:'2026-11-20T22:00:00Z',source_index_sha256:'a'.repeat(64),catalog_count:catalogs.length,record_count:records.length,metrics:{records_published:0,source_terms_reviewed_records:0},files:[{file:'registry.json.gz',bytes:registry.length,sha256:hash(registry),items:catalogs.length},{file:'records.json.gz',bytes:recordsFile.length,sha256:hash(recordsFile),items:records.length}]};
 return {manifest,registry,recordsFile,manifestRaw:Buffer.from(JSON.stringify(manifest))};
}

test('related records use canonical record IDs and deduplicate shared memberships',()=>{
 const network={
  records:[
   {record_id:'owner',title:'Owner record',record_type:'model',catalog_ids:['shared-a','shared-b'],source_urls:['https://example.com/owner']},
   {record_id:'related-a',title:'Related A',record_type:'model',catalog_ids:['shared-a'],source_urls:['https://example.com/a']},
   {record_id:'related-b',title:'Related B',record_type:'model',catalog_ids:['shared-a','shared-b'],source_urls:['https://example.com/b']},
   {record_id:'unrelated',title:'Unrelated',record_type:'place',catalog_ids:['other'],source_urls:['https://example.com/other']}
  ],
  catalogs:[
   {catalog_id:'shared-a',record_ids:['owner','related-a','related-b']},
   {catalog_id:'shared-b',record_ids:['owner','related-b']},
   {catalog_id:'other',record_ids:['unrelated']}
  ]
 };
 const result=resolveRelatedRecords('owner',network,1,0);
 assert.equal(result.owner_record.record_id,'owner');
 assert.equal(result.related_count,2);
 assert.equal(result.related_records.length,1);
 assert.equal(result.related_records[0].record_id,'related-a');
 assert.equal(result.has_more,true);
 const next=resolveRelatedRecords('owner',network,1,1);
 assert.equal(next.related_records[0].record_id,'related-b');
 assert.equal(resolveRelatedRecords('missing',network),null);
});

test('validates the compressed registry and global records against the manifest',()=>{
 const f=fixture(),network=validatePageDerivedCatalogs(f.manifest,f.registry,f.recordsFile);
 assert.equal(network.catalogCount,3);assert.equal(network.recordCount,3);assert.equal(network.sourceIndexPages,131899);assert.equal(network.metrics.records_published,0);
});
test('accepts the current 181,887-page search index snapshot',()=>{
 const f=fixture();f.manifest.source_index_pages=181887;
 const network=validatePageDerivedCatalogs(f.manifest,f.registry,f.recordsFile);
 assert.equal(network.sourceIndexPages,181887);
});
test('loads both artifacts from one commit-pinned GitHub snapshot',async()=>{
 const f=fixture(),sha='b'.repeat(40),base='https://raw.githubusercontent.com/rshure001/EverythingHub-Crawler/'+sha+'/data/page-derived-catalogs/';
 const artifacts=new Map([[base+'registry.json.gz',f.registry],[base+'records.json.gz',f.recordsFile]]);
 const loaded=await loadPageDerivedCatalogsFromManifest(f.manifest,sha,f.manifestRaw,async url=>{assert.ok(artifacts.has(url));return artifacts.get(url);});
 assert.equal(loaded.catalogCount,3);assert.equal(loaded.records.length,3);
});
test('loads bounded global record shards without requiring a monolithic records.json.gz',async()=>{
 const f=fixture(),sha='f'.repeat(40),all=JSON.parse(zlib.gunzipSync(f.recordsFile).toString('utf8')).records;
 const shards=[all.slice(0,2),all.slice(2)].map((records,index)=>gzip({version:1,shard:index,shard_count:2,records}));
 const entries=shards.map((raw,index)=>({file:'records/'+String(index).padStart(5,'0')+'.json.gz',bytes:raw.length,sha256:hash(raw),items:JSON.parse(zlib.gunzipSync(raw).toString('utf8')).records.length,format:'json'}));
 f.manifest.files=[f.manifest.files.find(x=>x.file==='registry.json.gz'),...entries];
 f.manifestRaw=Buffer.from(JSON.stringify(f.manifest));
 const network=validatePageDerivedCatalogs(f.manifest,f.registry,entries.map((entry,index)=>({file:entry.file,raw:shards[index]})));
 assert.equal(network.recordCount,3);
 assert.equal(network.records.length,3);
 assert.equal(network.records[0].record_id,'record-rolex');
 const base='https://raw.githubusercontent.com/rshure001/EverythingHub-Crawler/'+sha+'/data/page-derived-catalogs/';
 const artifacts=new Map([[base+'registry.json.gz',f.registry],...entries.map((entry,index)=>[base+entry.file.split('/').map(encodeURIComponent).join('/'),shards[index]])]);
 const loaded=await loadPageDerivedCatalogsFromManifest(f.manifest,sha,f.manifestRaw,async url=>{
  assert.ok(artifacts.has(url),'unexpected artifact request '+url);
  return artifacts.get(url);
 });
 assert.equal(loaded.catalogCount,3);
 assert.equal(loaded.records.length,3);
});
test('rejects a tampered registry before search',()=>{
 const f=fixture(),bad=Buffer.from(f.registry);bad[bad.length-1]^=1;
 assert.throws(()=>validatePageDerivedCatalogs(f.manifest,bad,f.recordsFile),/page_catalog_registry_sha256_mismatch|page_catalog_registry_gzip_invalid/);
});
test('rejects duplicate catalog and record IDs',()=>{
 const f=fixture(),payload=JSON.parse(zlib.gunzipSync(f.registry).toString('utf8'));
 payload.catalogs.push({...payload.catalogs[0]});
 const badRegistry=gzip(payload),m1={...f.manifest,catalog_count:4,files:f.manifest.files.map(x=>x.file==='registry.json.gz'?{...x,bytes:badRegistry.length,sha256:hash(badRegistry)}:x)};
 assert.throws(()=>validatePageDerivedCatalogs(m1,badRegistry,f.recordsFile),/page_catalog_identity_or_membership_invalid/);
 const recordPayload=JSON.parse(zlib.gunzipSync(f.recordsFile).toString('utf8'));recordPayload.records.push({...recordPayload.records[0]});
 const badRecords=gzip(recordPayload),m2={...f.manifest,record_count:4,files:f.manifest.files.map(x=>x.file==='records.json.gz'?{...x,bytes:badRecords.length,sha256:hash(badRecords)}:x)};
 assert.throws(()=>validatePageDerivedCatalogs(m2,f.registry,badRecords),/page_record_identity_or_provenance_invalid/);
});
test('Rolex favors the product catalog and excludes weak article-only mentions',()=>{
 const f=fixture(),network=validatePageDerivedCatalogs(f.manifest,f.registry,f.recordsFile),results=searchPageDerivedCatalogs('Rolex',network,5);
 assert.equal(results.length,1);assert.equal(results[0].catalog_id,'catalog-rolex');assert.match(results[0].title,/Rolex Submariner Date/);
 assert.equal(results[0].matched_records[0].record_type,'product');assert.equal(results[0].preview_only,true);assert.equal(results[0].commercial_eligible,false);
 assert.equal(results[0].source_terms_status,'not_reviewed');assert.equal('description' in results[0].matched_records[0],false);
});
test('search results carry the dedicated related-catalog identity for each record',()=>{
 const f=fixture(),network=validatePageDerivedCatalogs(f.manifest,f.registry,f.recordsFile),results=searchPageDerivedCatalogs('Rolex',network,5);
 assert.equal(results[0].matched_records[0].record_id,'record-rolex');
  assert.equal('record_catalog_id' in results[0].matched_records[0],false);
});

test('Las Vegas ranks the geographic place catalog',()=>{
 const f=fixture(),network=validatePageDerivedCatalogs(f.manifest,f.registry,f.recordsFile),results=searchPageDerivedCatalogs('Las Vegas',network,5);
 assert.equal(results.length,1);assert.equal(results[0].catalog_id,'catalog-vegas');assert.equal(results[0].matched_records[0].title,'Las Vegas Strip');assert.match(results[0].title,/Place catalog/);
});

test('Las Vegas prioritizes typed place and local directory catalogs over unrelated article collections',()=>{
 const catalogs=[
  {catalog_id:'articles',title:'National Geographic Articles',catalog_type:'knowledge_base',record_type:'article',source_url:'https://example.org/articles',record_count:1000,source_page_count:1000},
  {catalog_id:'places',title:'BLM Places',catalog_type:'geographic',record_type:'place',source_url:'https://example.gov/places',record_count:26,source_page_count:8},
  {catalog_id:'organizations',title:'BLM Organizations',catalog_type:'organization_directory',record_type:'organization',source_url:'https://example.gov/offices',record_count:131,source_page_count:137},
  {catalog_id:'events',title:'DOE Events',catalog_type:'events',record_type:'event',source_url:'https://example.gov/events',record_count:3,source_page_count:2}
 ];
 const records=[
  ...['Best places to stay in Las Vegas','Las Vegas in Photos','Las Vegas Must-Dos','A guide to Las Vegas','Las Vegas resorts'].map((title,i)=>({
   record_id:'article-'+i,record_type:'article',title,description:'A Las Vegas travel article.',
   source_urls:['https://example.org/article-'+i],catalog_ids:['articles'],attributes:{}
  })),
  {record_id:'place',record_type:'place',title:'Las Vegas Strip',description:'A destination in Las Vegas.',
   source_urls:['https://example.gov/las-vegas-strip'],catalog_ids:['places'],attributes:{}},
  {record_id:'office',record_type:'organization',title:'Las Vegas Field Office',description:'Local public office.',
   source_urls:['https://example.gov/las-vegas-office'],catalog_ids:['organizations'],attributes:{}},
  {record_id:'event',record_type:'event',title:'Las Vegas Grand Prix',description:'A major Las Vegas event.',
   source_urls:['https://example.gov/las-vegas-grand-prix'],catalog_ids:['events'],attributes:{}}
 ];
 const results=searchPageDerivedCatalogs('Las Vegas',{catalogs,records},5);
 assert.deepEqual(results.map(x=>x.catalog_id),['places','organizations']);
 assert.equal(results[0].relevance_class,'typed_location_match');
 assert.equal(results[1].relevance_class,'typed_location_match');
});
test('explicit Las Vegas events intent still returns the matching event catalog',()=>{
 const catalogs=[
  {catalog_id:'events',title:'DOE Events',catalog_type:'events',record_type:'event',source_url:'https://example.gov/events',record_count:3,source_page_count:2}
 ];
 const records=[
  {record_id:'event',record_type:'event',title:'Las Vegas Events Calendar',description:'A major Las Vegas event.',
   source_urls:['https://example.gov/las-vegas-grand-prix'],catalog_ids:['events'],attributes:{}}
 ];
 const results=searchPageDerivedCatalogs('Las Vegas events',{catalogs,records},5);
 assert.deepEqual(results.map(x=>x.catalog_id),['events']);
 assert.equal(results[0].relevance_class,'typed_event_match');
});
test('Rolex article or subject mentions are excluded from catalog previews when no product record exists',()=>{
 const results=searchPageDerivedCatalogs('Rolex',{
  catalogs:[{catalog_id:'mentions',title:'National Geographic Subjects',catalog_type:'knowledge_base',
   record_type:'subject',source_url:'https://example.org/subjects',record_count:7,source_page_count:7}],
  records:[{record_id:'mention',record_type:'subject',
   title:'National Geographic and Rolex Committed to a Perpetual Planet',
   description:'A conservation partnership mention.',source_urls:['https://example.org/rolex-mention'],
   catalog_ids:['mentions'],attributes:{}}]
 },5);
 assert.equal(results.length,0);
});
test('catalog preview links to the matching typed record instead of an unrelated first catalog source',()=>{
 const results=searchPageDerivedCatalogs('Las Vegas',{
  catalogs:[{catalog_id:'mixed',title:'Las Vegas Places',subject:'Las Vegas',catalog_type:'geographic',record_type:'place',source_url:'https://example.org/unrelated-first-record',record_count:100,source_page_count:100}],
  records:[
   {record_id:'other',record_type:'subject',title:'Unrelated catalog starter',description:'General knowledge record',source_urls:['https://example.org/unrelated-first-record'],catalog_ids:['mixed'],attributes:{}},
   {record_id:'match',record_type:'place',title:'Las Vegas Strip',description:'A matching Las Vegas destination',source_urls:['https://example.org/las-vegas-match'],catalog_ids:['mixed'],attributes:{}}
  ]
 },5);
 assert.equal(results.length,1);
 assert.equal(results[0].matched_records[0].source_url,'https://example.org/las-vegas-match');
 assert.equal(results[0].url,'https://example.org/las-vegas-match');
});

test('loads only matching sharded catalog indexes for Rolex and Las Vegas queries',async()=>{
 const f=fixture(),sha='c'.repeat(40),artifacts=new Map();
 const add=(file,buffer,items)=>{f.manifest.files.push({file,bytes:buffer.length,sha256:hash(buffer),items});artifacts.set('https://raw.githubusercontent.com/rshure001/EverythingHub-Crawler/'+sha+'/data/page-derived-catalogs/'+file.split('/').map(encodeURIComponent).join('/'),buffer);};
 for(const [id,title,recordId] of [['catalog-rolex','Rolex Watches','record-rolex'],['catalog-vegas','Nevada Places','record-vegas']]){
  const digest=crypto.createHash('sha256').update(id).digest('hex'),path='indexes/'+digest.slice(0,2)+'/'+digest.slice(0,24)+'.json.gz';
  const record=f.recordsFile;
  const records=JSON.parse(zlib.gunzipSync(record).toString('utf8')).records.filter(r=>r.record_id===recordId);
  add(path,gzip({version:1,catalog_id:id,title,status:'candidate_needs_source_validation',records}),records.length);
 }
 const terms=['rolex','las','vegas'];
 const byPrefix=new Map();
 for(const term of terms){const prefix=crypto.createHash('sha256').update(term).digest('hex').slice(0,2);if(!byPrefix.has(prefix))byPrefix.set(prefix,{});byPrefix.get(prefix)[term]=[term==='rolex'?'catalog-rolex':'catalog-vegas'];}
 for(const [prefix,tokens] of byPrefix)add('search-index/'+prefix+'.json.gz',gzip({version:1,prefix,tokens}),Object.keys(tokens).length);
 const manifestRaw=Buffer.from(JSON.stringify(f.manifest));
 const refUrl='https://api.github.com/repos/rshure001/EverythingHub-Crawler/git/ref/heads/main';
 const manifestUrl='https://raw.githubusercontent.com/rshure001/EverythingHub-Crawler/'+sha+'/data/page-derived-catalogs/manifest.json';
 const fetcher=async url=>{
  if(url.startsWith(refUrl))return Buffer.from(JSON.stringify({object:{sha}}));
  if(url===manifestUrl)return manifestRaw;
  if(artifacts.has(url))return artifacts.get(url);
  throw new Error('unexpected_test_url '+url);
 };
 const rolex=await loadQueryScopedPageDerivedCatalogs('Rolex',fetcher);
 assert.equal(rolex.catalogs.length,1);assert.equal(rolex.catalogs[0].catalog_id,'catalog-rolex');
 assert.equal(rolex.records[0].record_id,'record-rolex');
 const vegas=await loadQueryScopedPageDerivedCatalogs('Las Vegas',fetcher);
 assert.equal(vegas.catalogs.length,1);assert.equal(vegas.catalogs[0].catalog_id,'catalog-vegas');
 assert.equal(vegas.records[0].record_id,'record-vegas');
});

test('bounds query-scoped catalog index fan-out while keeping the first priority candidates',async()=>{
 const modulePath=require.resolve('../src/lib/page-derived-catalogs');
 delete require.cache[modulePath];
 const freshModule=require(modulePath);
 const loadFresh=freshModule.loadQueryScopedPageDerivedCatalogs;
 const f=fixture(),sha='d'.repeat(40),artifacts=new Map(),indexFetches=[];
 const candidates=Array.from({length:20},(_,index)=>index===0?'global-place':`distractor-${String(index).padStart(2,'0')}`);
 const add=(file,buffer,items)=>{f.manifest.files.push({file,bytes:buffer.length,sha256:hash(buffer),items});artifacts.set('https://raw.githubusercontent.com/rshure001/EverythingHub-Crawler/'+sha+'/data/page-derived-catalogs/'+file.split('/').map(encodeURIComponent).join('/'),buffer);};
 for(let index=0;index<candidates.length;index++){
  const id=candidates[index],digest=crypto.createHash('sha256').update(id).digest('hex');
  const record={record_id:'record-'+id,record_type:index===0?'place':'subject',title:'Las Vegas fanout probe '+id,description:'Las Vegas fanout probe candidate',source_urls:['https://example.test/'+id],source_page_ids:['page-'+id],catalog_ids:[id],commercial_eligible:false,source_terms_status:'not_reviewed',rights_status:'publicly_accessible_not_cleared_for_redistribution',attributes:{}};
  add('indexes/'+digest.slice(0,2)+'/'+digest.slice(0,24)+'.json.gz',gzip({version:1,catalog_id:id,title:index===0?'Global place directory':'Unrelated Las Vegas collection',status:'candidate_needs_source_validation',records:[record]}),1);
 }
 const byPrefix=new Map();
 for(const term of ['las','vegas','fanout','probe']){
  const prefix=crypto.createHash('sha256').update(term).digest('hex').slice(0,2);
  if(!byPrefix.has(prefix))byPrefix.set(prefix,{});
  byPrefix.get(prefix)[term]=candidates;
 }
 for(const [prefix,tokens] of byPrefix)add('search-index/'+prefix+'.json.gz',gzip({version:1,prefix,tokens}),Object.keys(tokens).length);
 const manifestRaw=Buffer.from(JSON.stringify(f.manifest));
 const refUrl='https://api.github.com/repos/rshure001/EverythingHub-Crawler/git/ref/heads/main';
 const manifestUrl='https://raw.githubusercontent.com/rshure001/EverythingHub-Crawler/'+sha+'/data/page-derived-catalogs/manifest.json';
 const fetcher=async url=>{
  if(url.startsWith(refUrl))return Buffer.from(JSON.stringify({object:{sha}}));
  if(url===manifestUrl)return manifestRaw;
  if(url.includes('/indexes/'))indexFetches.push(url);
  if(artifacts.has(url))return artifacts.get(url);
  throw new Error('unexpected_test_url '+url);
 };
 const loaded=await loadFresh('Las Vegas fanout probe',fetcher);
 assert.equal(loaded.queryCandidateCount,12);
 assert.equal(loaded.queryCatalogsLoaded,12);
 assert.equal(loaded.catalogs.length,12);
 assert.equal(indexFetches.length,12);
 assert.equal(loaded.catalogs[0].catalog_id,'global-place');
});

test('freshness requires matching source-page count and update timestamp',()=>{
 const {matchesPageIndexSnapshot}=require('../src/lib/page-derived-catalogs');
 const updatedAt=Date.parse('2026-11-20T22:00:00Z')/1000;
 assert.equal(matchesPageIndexSnapshot({sourceIndexPages:10,sourceIndexUpdatedAt:'2026-11-20T22:00:00Z'},{indexedPages:10,updatedAt}),true);
 assert.equal(matchesPageIndexSnapshot({sourceIndexPages:9,sourceIndexUpdatedAt:'2026-11-20T22:00:00Z'},{indexedPages:10,updatedAt}),false);
 assert.equal(matchesPageIndexSnapshot({sourceIndexPages:10,sourceIndexUpdatedAt:'2026-11-20T21:50:00Z'},{indexedPages:10,updatedAt}),false);
});

test('type-aware ranking puts product and place catalogs ahead of incidental content catalogs',()=>{
 const network={
  catalogs:[
   {catalog_id:'rolex-news',title:'Rolex News',catalog_type:'knowledge_base',record_type:'article',source_url:'https://example.org/rolex-news',record_count:1,source_page_count:1},
   {catalog_id:'rolex-models',title:'Rolex Models',catalog_type:'commerce',record_type:'model',source_url:'https://example.com/rolex-models',record_count:1,source_page_count:1},
   {catalog_id:'vegas-news',title:'Las Vegas Events',catalog_type:'knowledge_base',record_type:'article',source_url:'https://example.org/vegas-news',record_count:1,source_page_count:1},
   {catalog_id:'vegas-places',title:'Nevada Places',catalog_type:'geographic',record_type:'place',source_url:'https://example.net/vegas-places',record_count:1,source_page_count:1}
  ],
  records:[
   {record_id:'rolex-news-record',record_type:'article',title:'Rolex News',description:'A news article about watches',source_urls:['https://example.org/rolex-news'],catalog_ids:['rolex-news'],attributes:{}},
   {record_id:'rolex-model-record',record_type:'model',title:'Submariner',description:'Rolex watch model',source_urls:['https://example.com/submariner'],catalog_ids:['rolex-models'],attributes:{brand:'Rolex'}},
   {record_id:'vegas-news-record',record_type:'article',title:'Las Vegas Championship',description:'Sports coverage from Las Vegas',source_urls:['https://example.org/vegas-game'],catalog_ids:['vegas-news'],attributes:{}},
   {record_id:'vegas-place-record',record_type:'place',title:'Las Vegas Strip',description:'The Las Vegas Strip in Nevada',source_urls:['https://example.net/las-vegas-strip'],catalog_ids:['vegas-places'],attributes:{}}
  ]
 };
 assert.equal(searchPageDerivedCatalogs('Rolex',network,5)[0].catalog_id,'rolex-models');
 assert.equal(searchPageDerivedCatalogs('Las Vegas',network,5)[0].catalog_id,'vegas-places');
});


test('loads the live catalog manifest after index files exceed twenty thousand and four megabytes', async () => {
 const f=fixture();
 const extraFiles=Array.from({length:20124},(_,index)=>({
  file:'search-index/extra-'+String(index).padStart(5,'0')+'-'+'x'.repeat(120)+'.json.gz',
  bytes:1,sha256:'a'.repeat(64),items:0
 }));
 f.manifest.files.push(...extraFiles);
 const manifestRaw=Buffer.from(JSON.stringify(f.manifest));
 assert.ok(manifestRaw.length>4000000 && manifestRaw.length<8000000);
 const sha='e'.repeat(40);
 const loaded=await loadPageDerivedCatalogsFromManifest(f.manifest,sha,manifestRaw,async url=>{
  if(url.endsWith('/registry.json.gz'))return f.registry;
  if(url.endsWith('/records.json.gz'))return f.recordsFile;
  throw new Error('unexpected_test_url '+url);
 });
 assert.equal(loaded.catalogCount,3);
 assert.equal(loaded.records.length,3);
});

test('rejects a page-catalog manifest beyond the forty-thousand-file safety ceiling', () => {
 const f=fixture();
 f.manifest.files=Array.from({length:40001},(_,index)=>({
  file:'search-index/overflow-'+String(index)+'.json.gz',
  bytes:1,sha256:'a'.repeat(64),items:0
 }));
 assert.throws(
  ()=>validatePageDerivedCatalogs(f.manifest,f.registry,f.recordsFile),
  /page_catalog_manifest_invalid/
 );
});

test('rejects page-catalog manifest bytes beyond the eight-megabyte safety ceiling', async () => {
 const f=fixture();
 const oversized=Buffer.from(JSON.stringify(f.manifest).padEnd(8000001,' '));
 await assert.rejects(
  loadPageDerivedCatalogsFromManifest(f.manifest,'f'.repeat(40),oversized,async()=>f.registry),
  /page_catalog_manifest_raw_size_invalid/
 );
});
