(function(){
"use strict";
const MANIFEST="https://raw.githubusercontent.com/rshure001/EverythingHub-Crawler/main/public-search/manifest.json";
const $=id=>document.getElementById(id);
const form=$("searchForm"),query=$("query"),status=$("status"),results=$("results"),tools=$("tools");
const buttons=[...document.querySelectorAll(".mode")];
let mode="Web",activeRun=0;
const laneFor={News:"news",Business:"business",Research:"science"};
function esc(s){return String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}
function externalLinks(q){
 const e=encodeURIComponent(q);
 const urls={
  Web:"https://www.bing.com/search?q="+e,
  News:"https://www.bing.com/news/search?q="+e,
  Images:"https://www.bing.com/images/search?q="+e,
  Videos:"https://www.bing.com/videos/search?q="+e,
  Shopping:"https://www.bing.com/shop?q="+e,
  Places:"https://www.bing.com/maps?q="+e,
  Business:"https://www.bing.com/search?q="+encodeURIComponent(q+" business"),
  Research:"https://www.bing.com/search?q="+encodeURIComponent(q+" research")
 };
 tools.innerHTML="";
 if(urls[mode]){
  const a=document.createElement("a");a.href=urls[mode];a.target="_blank";a.rel="noopener noreferrer";
  a.textContent="Open "+mode+" results";tools.appendChild(a);tools.hidden=false;
 }else{tools.hidden=true;}
}
function render(items){
 if(!items.length){results.innerHTML='<div class="result"><strong>No indexed matches for this search.</strong><p class="desc">Try a shorter phrase or open the broader web search.</p></div>';return;}
 results.innerHTML=items.map(item=>'<article class="result"><a href="'+esc(item.url)+'" target="_blank" rel="noopener noreferrer">'+esc(item.title||item.url)+'</a><div class="url">'+esc(item.url)+'</div><p class="desc">'+esc(item.description||item.snippet||"")+'</p></article>').join("");
}
async function search(){
 const q=query.value.trim();if(q.length<2){status.textContent="Enter at least two characters.";return;}
 const run=++activeRun;results.innerHTML="";tools.hidden=true;status.textContent="Searching the verified Everything Hub index…";
 if(!window.EverythingHubSearch){status.textContent="The search engine script could not load. Open broad web search below.";externalLinks(q);return;}
 try{
  const engine=await window.EverythingHubSearch.create({
   query:q,manifestUrl:MANIFEST,maxParallel:4,
   onProgress:p=>{if(run===activeRun)status.textContent="Checking "+p.loadedShards+" of "+p.totalShards+" relevant index shards…";}
  });
  if(run!==activeRun)return;
  const items=engine.search(q,{limit:15,lane:laneFor[mode]||undefined});
  render(items);
  status.textContent=items.length+" indexed results · "+engine.candidatePageCount+" candidate pages checked · "+engine.selectedShardCount+" verified shards · "+mode;
  externalLinks(q);
 }catch(err){
  if(run!==activeRun)return;
  status.textContent="The independent index could not be reached: "+String(err.message||err).slice(0,180);
  results.innerHTML='<div class="result"><strong>Local index temporarily unavailable.</strong><p class="desc">The static site is running independently; use the broader search link while the index source recovers.</p></div>';
  const a=document.createElement("a");a.href="https://www.bing.com/search?q="+encodeURIComponent(q);a.target="_blank";a.rel="noopener noreferrer";a.textContent="Open broader web search";tools.innerHTML="";tools.appendChild(a);tools.hidden=false;
 }
}
form.addEventListener("submit",e=>{e.preventDefault();search();});
buttons.forEach(b=>b.addEventListener("click",()=>{mode=b.dataset.mode;buttons.forEach(x=>x.classList.toggle("active",x===b));if(query.value.trim())search();else{status.textContent=mode+" search selected. Enter a query to search.";tools.hidden=true;}}));
const initial=new URLSearchParams(location.search).get("q");if(initial){query.value=initial;search();}
})();