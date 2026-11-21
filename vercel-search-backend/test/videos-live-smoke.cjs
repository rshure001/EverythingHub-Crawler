"use strict";
const assert=require("node:assert/strict");
const {fetchArchiveVideos}=require("../src/api/videos")._test;
(async()=>{
  const candidates=["documentary","animation","education","film","space","NASA"];
  const report=[];
  let selected=null,items=[];
  for(const query of candidates){
    const results=await fetchArchiveVideos(query);
    report.push({query,count:results.length,titles:results.slice(0,3).map(item=>item.title),licenses:results.slice(0,3).map(item=>item.license)});
    if(results.length>=5){selected=query;items=results;break;}
  }
  console.log(JSON.stringify({provider:"Internet Archive",robotsCheckedHost:"archive.org",candidateQueries:report,selectedQuery:selected,returnedVideos:items.length},null,2));
  assert.ok(selected,"No test query returned five robots-approved, attributed, explicitly licensed video records");
  for(const item of items){
    assert.ok(item.title,"Video title missing");
    assert.ok(Array.isArray(item.matchedTerms)&&item.matchedTerms.length>0,"Video must match query in title, subject or creator");
    assert.ok(item.url.startsWith("https://archive.org/details/"),"Video link is not an Internet Archive item page");
    assert.ok(item.thumbnail.startsWith("https://archive.org/services/img/"),"Thumbnail is not an Internet Archive preview");
    assert.ok(/^(CC0|Public Domain Mark|CC BY)/i.test(item.license),"License is outside the allowed open-license set");
    assert.ok(item.licenseUrl.startsWith("https://creativecommons.org/"),"License URL must be canonical HTTPS");
    if(/^CC BY/i.test(item.license))assert.ok(item.creator,"CC BY video requires creator attribution");
  }
})().catch(error=>{console.error("Internet Archive video live smoke failed:",error&&error.message?error.message:String(error));process.exitCode=1;});
