"use strict";
const test=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path");
const {searchExternalCatalogs,validateExternalCatalogs,getExternalCatalogStats}=require("../src/lib/external-catalogs");
const repoData=JSON.parse(fs.readFileSync(path.join(__dirname,"../../data/external-catalogs/registry.json"),"utf8"));
const backendData=require("../src/data/external-catalogs.json");
test("external Rolex link catalog is separate, stable and rights-restricted",()=>{assert.deepEqual(validateExternalCatalogs(backendData),{catalogCount:1,recordCount:6});assert.deepEqual(repoData,backendData);const catalog=backendData.catalogs[0];assert.equal(catalog.catalog_id,"external:rolex:official-collections");assert.equal(catalog.source_page_count,0);assert.equal(catalog.content_copied,false);assert.equal(catalog.commercial_eligible,false);});
test("Rolex search returns a rich official collection directory with six links",()=>{const results=searchExternalCatalogs("Rolex");assert.equal(results.length,1);assert.equal(results[0].catalog_id,"external:rolex:official-collections");assert.equal(results[0].matched_record_count,6);assert.ok(results[0].matched_records.some(record=>record.title==="Rolex Submariner"));assert.ok(results[0].matched_records.some(record=>record.title==="Rolex Datejust"));assert.equal(results[0].preview_only,true);assert.equal(results[0].commercial_eligible,false);});
test("specific Rolex family search returns only the matching link",()=>{const results=searchExternalCatalogs("Rolex Submariner");assert.equal(results.length,1);assert.equal(results[0].matched_record_count,1);assert.equal(results[0].matched_records[0].record_id,"external:rolex:collection:submariner");});
test("unrelated searches do not receive Rolex results",()=>{assert.deepEqual(searchExternalCatalogs("NASA"),[]);assert.deepEqual(searchExternalCatalogs("Las Vegas"),[]);});
test("duplicate external catalog and record IDs are rejected",()=>{const bad={catalogs:[backendData.catalogs[0],backendData.catalogs[0]]};assert.throws(()=>validateExternalCatalogs(bad),/duplicate/);});

test("external catalog metrics remain separate from page-derived counts",()=>{assert.deepEqual(getExternalCatalogStats(),{catalogCount:1,recordCount:6});});
