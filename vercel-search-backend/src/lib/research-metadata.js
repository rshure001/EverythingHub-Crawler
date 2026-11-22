'use strict';

const https = require('node:https');

const MAX_QUERY_LENGTH = 160;
const MAX_RESULTS_PER_SOURCE = 10;
const MAX_RESPONSE_BYTES = 1500000;
const REQUEST_TIMEOUT_MS = 6500;
const CACHE_TTL_MS = 5 * 60 * 1000;
const MAX_CACHE_ENTRIES = 200;
const USER_AGENT = 'EverythingHub-ResearchSearch/1.0 (https://everythinghub.org)';
const cache = new Map();
const inFlight = new Map();
let crossrefQueue = Promise.resolve();
let lastCrossrefRequestAt = 0;

function clean(value, limit = 320) {
  return String(value || '').replace(/<[^>]*>/g, ' ').replace(/\\s+/g, ' ').trim().slice(0, limit);
}

function firstTitle(value) {
  if (Array.isArray(value)) return clean(value[0]);
  return clean(value);
}

function yearFromParts(value) {
  const parts = value && Array.isArray(value['date-parts']) ? value['date-parts'][0] : null;
  return Array.isArray(parts) && Number.isInteger(Number(parts[0])) ? String(parts[0]) : '';
}

function mapCrossrefItem(item) {
  if (!item || typeof item !== 'object') return null;
  const doi = clean(item.DOI, 240).toLowerCase();
  const title = firstTitle(item.title);
  if (!doi || !title) return null;
  const authors = (Array.isArray(item.author) ? item.author : [])
    .slice(0, 4)
    .map(person => clean([person && person.given, person && person.family].filter(Boolean).join(' '), 100))
    .filter(Boolean);
  const publisher = clean(item.publisher, 180);
  const year = yearFromParts(item.published || item.issued || item.created);
  const licenses = (Array.isArray(item.license) ? item.license : [])
    .map(value => ({ url: clean(value && value.URL, 500), contentVersion: clean(value && value['content-version'], 80) }))
    .filter(value => value.url);
  return {
    id: 'crossref:' + doi,
    doi,
    title,
    url: 'https://doi.org/' + doi,
    snippet: [authors.join(', '), publisher, year, clean(item.type, 80)].filter(Boolean).join(' · '),
    source: 'Crossref',
    origin: 'Crossref Bibliographic Metadata',
    provider: 'crossref-metadata',
    mediaType: 'research',
    resourceType: clean(item.type, 80),
    publisher,
    authors,
    year,
    rights: licenses,
    metadataLicense: 'Public-domain bibliographic facts; abstracts excluded',
    abstractIncluded: false
  };
}

function mapDataCiteItem(item) {
  if (!item || typeof item !== 'object') return null;
  const attributes = item.attributes && typeof item.attributes === 'object' ? item.attributes : {};
  const doi = clean(attributes.doi || item.id, 240).toLowerCase();
  const title = firstTitle((attributes.titles || []).map(value => value && value.title));
  if (!doi || !title) return null;
  const authors = (Array.isArray(attributes.creators) ? attributes.creators : [])
    .slice(0, 4)
    .map(person => clean(person && (person.name || [person.givenName, person.familyName].filter(Boolean).join(' ')), 100))
    .filter(Boolean);
  const publisher = clean(attributes.publisher, 180);
  const year = clean(attributes.publicationYear, 12);
  const types = attributes.types || {};
  const resourceType = clean(types.resourceTypeGeneral || types.resourceType, 80);
  const rights = (Array.isArray(attributes.rightsList) ? attributes.rightsList : [])
    .map(value => ({ label: clean(value && value.rights, 180), url: clean(value && value.rightsUri, 500) }))
    .filter(value => value.label || value.url);
  return {
    id: 'datacite:' + doi,
    doi,
    title,
    url: 'https://doi.org/' + doi,
    snippet: [authors.join(', '), publisher, year, resourceType].filter(Boolean).join(' · '),
    source: 'DataCite',
    origin: 'DataCite DOI Metadata',
    provider: 'datacite-metadata',
    mediaType: 'research',
    resourceType,
    publisher,
    authors,
    year,
    rights,
    metadataLicense: 'CC0 1.0 DataCite metadata; linked resources retain their own rights',
    abstractIncluded: false
  };
}

function mergeResearchResults(crossrefItems, dataCiteItems, limit = 20) {
  const seen = new Set();
  const merged = [];
  for (const item of [...(crossrefItems || []), ...(dataCiteItems || [])]) {
    const doi = String(item && item.doi || '').trim().toLowerCase();
    if (!doi || seen.has(doi)) continue;
    seen.add(doi);
    merged.push({ ...item, doi });
    if (merged.length >= limit) break;
  }
  return merged;
}

function providerUrls(query) {
  const value = String(query || '').trim().slice(0, MAX_QUERY_LENGTH);
  const crossref = new URL('https://api.crossref.org/works');
  crossref.searchParams.set('query.bibliographic', value);
  crossref.searchParams.set('rows', String(MAX_RESULTS_PER_SOURCE));
  crossref.searchParams.set('select', 'DOI,title,author,publisher,type,created,issued,URL,subject,license');
  const dataCite = new URL('https://api.datacite.org/dois');
  dataCite.searchParams.set('query', value);
  dataCite.searchParams.set('page[size]', String(MAX_RESULTS_PER_SOURCE));
  dataCite.searchParams.set('fields[dois]', 'doi,titles,creators,publisher,publicationYear,types,subjects,dates,rightsList,url');
  return { crossref: crossref.toString(), dataCite: dataCite.toString() };
}

function requestJson(url, provider) {
  return new Promise((resolve, reject) => {
    let request;
    const chunks = [];
    let total = 0;
    request = https.get(url, {
      headers: {
        'user-agent': USER_AGENT,
        accept: 'application/json'
      }
    }, response => {
      if (response.statusCode < 200 || response.statusCode >= 300) {
        let errorBody = '';
        response.on('data', chunk => {
          if (errorBody.length < 4000) errorBody += chunk.toString('utf8').slice(0, 4000 - errorBody.length);
        });
        response.on('end', () => reject(new Error(provider + '_http_' + response.statusCode + ':' + errorBody)));
        response.on('error', reject);
        return;
      }
      const declared = Number(response.headers['content-length'] || 0);
      if (declared > MAX_RESPONSE_BYTES) {
        response.resume();
        reject(new Error(provider + '_response_too_large'));
        return;
      }
      response.on('data', chunk => {
        total += chunk.length;
        if (total > MAX_RESPONSE_BYTES) {
          request.destroy(new Error(provider + '_response_too_large'));
          return;
        }
        chunks.push(chunk);
      });
      response.on('end', () => {
        try {
          resolve(JSON.parse(Buffer.concat(chunks, total).toString('utf8')));
        } catch {
          reject(new Error(provider + '_invalid_json'));
        }
      });
      response.on('error', reject);
    });
    request.setTimeout(REQUEST_TIMEOUT_MS, () => request.destroy(new Error(provider + '_timeout')));
    request.on('error', reject);
  });
}

function requestCrossref(url) {
  const task = crossrefQueue.then(async () => {
    const wait = Math.max(0, 1000 - (Date.now() - lastCrossrefRequestAt));
    if (wait) await new Promise(resolve => setTimeout(resolve, wait));
    lastCrossrefRequestAt = Date.now();
    return requestJson(url, 'crossref');
  });
  crossrefQueue = task.catch(() => {});
  return task;
}

function remember(key, results) {
  cache.set(key, { loadedAt: Date.now(), results });
  while (cache.size > MAX_CACHE_ENTRIES) cache.delete(cache.keys().next().value);
  return results;
}

async function loadResearchMetadata(query) {
  const value = String(query || '').trim().slice(0, MAX_QUERY_LENGTH);
  if (!value) return [];
  const key = value.toLowerCase();
  const cached = cache.get(key);
  if (cached && Date.now() - cached.loadedAt < CACHE_TTL_MS) return cached.results;
  if (inFlight.has(key)) return inFlight.get(key);
  const urls = providerUrls(value);
  const task = Promise.allSettled([
    requestCrossref(urls.crossref),
    requestJson(urls.dataCite, 'datacite')
  ]).then(([crossrefResult, dataCiteResult]) => {
    const crossrefItems = crossrefResult.status === 'fulfilled'
      ? ((crossrefResult.value.message || {}).items || []).map(mapCrossrefItem).filter(Boolean)
      : [];
    const dataCiteItems = dataCiteResult.status === 'fulfilled'
      ? (Array.isArray(dataCiteResult.value.data) ? dataCiteResult.value.data : []).map(mapDataCiteItem).filter(Boolean)
      : [];
    const merged = mergeResearchResults(crossrefItems, dataCiteItems);
    return crossrefResult.status === 'fulfilled' || dataCiteResult.status === 'fulfilled'
      ? remember(key, merged)
      : merged;
  }).finally(() => inFlight.delete(key));
  inFlight.set(key, task);
  return task;
}

function resetForTests() {
  cache.clear();
  inFlight.clear();
  crossrefQueue = Promise.resolve();
  lastCrossrefRequestAt = 0;
}

module.exports = {
  loadResearchMetadata,
  mapCrossrefItem,
  mapDataCiteItem,
  mergeResearchResults,
  providerUrls,
  _test: { resetForTests, requestJson, CACHE_TTL_MS, MAX_RESPONSE_BYTES }
};
