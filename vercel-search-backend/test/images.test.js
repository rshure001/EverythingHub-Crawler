"use strict";
const test=require("node:test"),assert=require("node:assert/strict"),handler=require("../src/api/images");
const {parseRobots,normalizeArchiveLicense,normalizeArchiveImage,resetForTests}=handler._test;
function response(status,body,url){return{status,ok:status>=200&&status<300,url,async text(){return body;}};}
function imageDoc(index,overrides={}){return{identifier:"nasa-image-"+index,title:"NASA public image "+index,creator:"NASA",licenseurl:"https://creativecommons.org/licenses/by/4.0/",description:"NASA space public image",subject:["NASA","space"],format:["JPEG"],mediatype:"image",imagecount:1,date:"2024-01-01",...overrides};}
function mockResponse(){return{statusCode:0,headers:{},body:null,setHeader(name,value){this.headers[name]=value;return this;},status(code){this.statusCode=code;return this;},json(value){this.body=value;return this;},end(){return this;}};}
test("robots parser allows archive image metadata and thumbnail paths",()=>{resetForTests();const policy=parseRobots("User-agent: *\nDisallow: /control/\nDisallow: /report/\nCrawl-delay: 1");assert.equal(policy.allows("https://archive.org/advancedsearch.php?q=mediatype%3Aimage"),true);assert.equal(policy.allows("https://archive.org/services/img/nasa-image-1"),true);assert.equal(policy.allows("https://archive.org/control/item"),false);assert.equal(policy.delayMs,1000);});
test("image normalizer requires item-level CC0, PDM, CC BY or CC BY-SA and attribution",()=>{assert.equal(normalizeArchiveLicense("https://creativecommons.org/licenses/by/4.0/").name,"CC BY 4.0");assert.equal(normalizeArchiveImage(imageDoc(1),["nasa"]).source,"Internet Archive");assert.equal(normalizeArchiveImage(imageDoc(2,{creator:""}),["nasa"]),null);assert.equal(normalizeArchiveImage(imageDoc(3,{licenseurl:"https://creativecommons.org/licenses/by-nc/4.0/"}),["nasa"]),null);assert.equal(normalizeArchiveImage(imageDoc(4,{mediatype:"movies"}),["nasa"]),null);assert.equal(normalizeArchiveImage(imageDoc(5,{identifier:"../bad"}),["nasa"]),null);});
test("image metadata search checks robots, deduplicates IDs and never downloads files",async()=>{resetForTests();const originalFetch=global.fetch,calls=[];global.fetch=async url=>{const parsed=new URL(url);calls.push(parsed.href);if(parsed.hostname==="archive.org"&&parsed.pathname==="/robots.txt")return response(200,"User-agent: *\nDisallow: /control/\nDisallow: /report/\n",parsed.href);if(parsed.hostname==="archive.org"&&parsed.pathname==="/advancedsearch.php"){const docs=Array.from({length:12},(_,index)=>imageDoc(index));docs.push(imageDoc(0));return response(200,JSON.stringify({response:{docs}}),parsed.href);}throw new Error("Unexpected external request: "+parsed.href);};try{const res=mockResponse();await handler({method:"GET",query:{q:"NASA"}},res);assert.equal(res.statusCode,200);assert.equal(res.body.provider,"Internet Archive");assert.equal(res.body.count,12);assert.ok(res.body.items.every(item=>item.url.startsWith("https://archive.org/details/")));assert.ok(res.body.items.every(item=>item.thumbnail.startsWith("https://archive.org/services/img/")));assert.ok(res.body.items.every(item=>item.licenseUrl.startsWith("https://creativecommons.org/")));assert.equal(calls.length,2);assert.ok(calls.every(url=>!new URL(url).pathname.startsWith("/download/")));}finally{global.fetch=originalFetch;resetForTests();}});
test("image search pages through unfiltered Archive metadata before applying local license checks",async()=>{
  resetForTests();
  const originalFetch=global.fetch,apiCalls=[];
  global.fetch=async url=>{
    const parsed=new URL(url);
    if(parsed.pathname==="/robots.txt")return response(200,"User-agent: *\nDisallow: /control/\nDisallow: /report/\n",parsed.href);
    if(parsed.pathname==="/advancedsearch.php"){
      apiCalls.push(parsed.href);
      const page=Number(parsed.searchParams.get("page")||"1");
      const docs=page===1
        ? Array.from({length:100},(_,index)=>imageDoc(index+200,{licenseurl:"https://creativecommons.org/licenses/by-nc/4.0/"}))
        : Array.from({length:12},(_,index)=>imageDoc(index+400));
      return response(200,JSON.stringify({response:{docs}}),parsed.href);
    }
    throw new Error("Unexpected request: "+parsed.href);
  };
  try{
    const res=mockResponse();
    await handler({method:"GET",query:{q:"NASA"}},res);
    assert.equal(res.statusCode,200);
    assert.equal(res.body.count,12);
    assert.equal(apiCalls.length,2);
    assert.ok(apiCalls.every(url=>!new URL(url).searchParams.get("q").includes("licenseurl")));
  }finally{global.fetch=originalFetch;resetForTests();}
});

test("robots denial blocks the Archive image search API and fails closed",async()=>{resetForTests();const originalFetch=global.fetch,calls=[];global.fetch=async url=>{calls.push(String(url));return response(200,"User-agent: *\nDisallow: /advancedsearch.php\n",String(url));};try{const res=mockResponse();await handler({method:"GET",query:{q:"NASA"}},res);assert.equal(res.statusCode,503);assert.equal(res.body.status,"unavailable");assert.equal(calls.length,1);}finally{global.fetch=originalFetch;resetForTests();}});

test("robots denial of archive thumbnail path suppresses image cards",async()=>{resetForTests();const originalFetch=global.fetch;global.fetch=async url=>{const parsed=new URL(url);if(parsed.pathname==="/robots.txt")return response(200,"User-agent: *\nDisallow: /services/img/\n",parsed.href);if(parsed.pathname==="/advancedsearch.php")return response(200,JSON.stringify({response:{docs:[imageDoc(90)]}}),parsed.href);throw new Error("Unexpected request "+parsed.href);};try{const res=mockResponse();await handler({method:"GET",query:{q:"NASA"}},res);assert.equal(res.statusCode,200);assert.equal(res.body.count,0);}finally{global.fetch=originalFetch;resetForTests();}});

test("generic provider names are not accepted as CC BY creator attribution",()=>{assert.equal(normalizeArchiveImage(imageDoc(6,{creator:"Internet Archive"}),["nasa"]),null);const publicDomain=normalizeArchiveImage(imageDoc(7,{creator:"Internet Archive",licenseurl:"https://creativecommons.org/publicdomain/mark/1.0/"}),["nasa"]);assert.ok(publicDomain);assert.equal(publicDomain.creator,"");});

test("image normalizer rejects explicit missing images but tolerates absent count metadata",()=>{
  assert.equal(normalizeArchiveImage(imageDoc(8,{imagecount:0}),["nasa"]),null);
  const unknownCount=normalizeArchiveImage(imageDoc(9,{imagecount:undefined}),["nasa"]);
  assert.ok(unknownCount);
  assert.equal(unknownCount.imageCount,null);
  assert.equal(normalizeArchiveImage(imageDoc(10,{identifier:"megamap"}),["nasa"]),null);
  const item=normalizeArchiveImage(imageDoc(11,{imagecount:"2"}),["nasa"]);
  assert.ok(item);
  assert.equal(item.imageCount,2);
});
