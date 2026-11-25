import "jsr:@supabase/functions-js/edge-runtime.d.ts";
const bucket = "everythinghub-public-site";
Deno.serve(async (req: Request) => {
  if (req.method !== "GET" && req.method !== "POST") return Response.json({error:"GET or POST only"},{status:405});
  const projectUrl = Deno.env.get("SUPABASE_URL") || "https://vdceayvfgkwpgwixrhwi.supabase.co";
  const key = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";
  if (!key) return Response.json({error:"SUPABASE_SERVICE_ROLE_KEY is not available to this function. Storage upload cannot be authorized."},{status:503});
  const headers = {apikey:key,Authorization:"Bearer "+key};
async function verifyGitHubActionsToken(token: string, expectedSha: string) {
  const decode = (part: string) => {
    const normalized = part.replace(/-/g, "+").replace(/_/g, "/");
    const padded = normalized + "=".repeat((4 - normalized.length % 4) % 4);
    return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
  };
  const parse = (part: string) => JSON.parse(new TextDecoder().decode(decode(part)));
  const parts = token.split(".");
  if (parts.length !== 3) throw new Error("Malformed GitHub OIDC token");
  const header = parse(parts[0]);
  const claims = parse(parts[1]);
  if (header.alg !== "RS256" || !header.kid) throw new Error("Unsupported GitHub OIDC signing algorithm");
  const now = Math.floor(Date.now() / 1000);
  const audiences = Array.isArray(claims.aud) ? claims.aud : [claims.aud];
  const expectedWorkflow = "rshure001/EverythingHub-Crawler/.github/workflows/everything-hub-publish-and-verify.yml@refs/heads/main";
  if (claims.iss !== "https://token.actions.githubusercontent.com") throw new Error("Unexpected OIDC issuer");
  if (!audiences.includes("supabase-static-publisher")) throw new Error("Unexpected OIDC audience");
  if (claims.exp < now || (claims.nbf && claims.nbf > now + 30)) throw new Error("GitHub OIDC token is expired or not yet valid");
  if (claims.repository !== "rshure001/EverythingHub-Crawler" || claims.repository_owner !== "rshure001") throw new Error("OIDC repository is not authorized");
  if (claims.ref !== "refs/heads/main" || claims.workflow_ref !== expectedWorkflow) throw new Error("OIDC workflow or branch is not authorized");
  if (!["push", "workflow_dispatch"].includes(claims.event_name)) throw new Error("OIDC event is not authorized");
  if (claims.sha !== expectedSha) throw new Error("OIDC commit SHA does not match the requested release");
  const jwksResponse = await fetch("https://token.actions.githubusercontent.com/.well-known/jwks");
  if (!jwksResponse.ok) throw new Error("Could not retrieve GitHub OIDC signing keys");
  const jwks = await jwksResponse.json();
  const jwk = Array.isArray(jwks.keys) ? jwks.keys.find((key: any) => key.kid === header.kid && key.kty === "RSA") : null;
  if (!jwk) throw new Error("GitHub OIDC signing key was not found");
  const publicKey = await crypto.subtle.importKey(
    "jwk",
    jwk,
    { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
    false,
    ["verify"]
  );
  const valid = await crypto.subtle.verify(
    { name: "RSASSA-PKCS1-v1_5" },
    publicKey,
    decode(parts[2]),
    new TextEncoder().encode(parts[0] + "." + parts[1])
  );
  if (!valid) throw new Error("GitHub OIDC signature verification failed");
  return claims;
}
  const requestUrl = new URL(req.url);

  // A public diagnostic probe is strictly read-only: it never creates a bucket or uploads a page.
  if (req.method === "GET" && requestUrl.searchParams.has("probe")) {
    try {
      const list = await fetch(projectUrl + "/storage/v1/bucket", { headers });
      if (!list.ok) {
        return Response.json({
          status: "unavailable",
          error: "Storage bucket listing failed",
          storageStatus: list.status,
          detail: (await list.text()).slice(0, 800),
          publishOnProbe: false
        }, { status: 502 });
      }
      const buckets = await list.json();
      const bucketExists = Array.isArray(buckets) && buckets.some((b: any) => b.name === bucket);
      return Response.json({
        status: bucketExists ? "ready" : "bucket_missing",
        bucket,
        bucketExists,
        publishOnProbe: false
      });
    } catch (e) {
      return Response.json({
        status: "unavailable",
        error: "Publisher probe failed",
        detail: e instanceof Error ? e.message : String(e),
        publishOnProbe: false
      }, { status: 502 });
    }
  }

  // GET is read-only unless it is the explicit diagnostic probe above.
  if (req.method === "GET") {
    return Response.json({ error: "GET is read-only. Use an authenticated POST to publish." }, { status: 405 });
  }

  const authHeader = req.headers.get("Authorization") || "";
  const token = authHeader.replace(/^Bearer\\s+/i, "");
  if (!token) return Response.json({ error: "GitHub Actions OIDC identity required." }, { status: 401 });
  const publishRequest = await req.json().catch(() => ({}));
  const sha = typeof publishRequest.sha === "string" && /^[0-9a-f]{40}$/i.test(publishRequest.sha) ? publishRequest.sha : "";
  if (!sha) return Response.json({ error: "A 40-character release commit SHA is required." }, { status: 400 });
  let publisherClaims: any;
  try {
    publisherClaims = await verifyGitHubActionsToken(token, sha);
  } catch (e) {
    return Response.json({ error: "GitHub Actions OIDC verification failed", detail: e instanceof Error ? e.message : String(e) }, { status: 401 });
  }

  try {
    const list = await fetch(projectUrl+"/storage/v1/bucket",{headers});
    if (!list.ok) return Response.json({error:"Storage bucket listing failed",status:list.status,detail:(await list.text()).slice(0,800)},{status:502});
    const buckets = await list.json();
    if (!Array.isArray(buckets) || !buckets.some((b:any)=>b.name===bucket)) {
      const create = await fetch(projectUrl+"/storage/v1/bucket",{method:"POST",headers:{...headers,"content-type":"application/json"},body:JSON.stringify({name:bucket,public:true,file_size_limit:5242880})});
      if (!create.ok && create.status!==409) return Response.json({error:"Storage bucket creation failed",status:create.status,detail:(await create.text()).slice(0,800)},{status:502});
    }
    const rawBase = "https://raw.githubusercontent.com/rshure001/EverythingHub-Crawler/" + sha + "/site/";
    const [htmlResponse, searchResponse] = await Promise.all([fetch(rawBase + "index.html"), fetch(rawBase + "search.js")]);
    if (!htmlResponse.ok || !searchResponse.ok) return Response.json({error:"Release source fetch failed",htmlStatus:htmlResponse.status,searchStatus:searchResponse.status,sha},{status:502});
    const html = await htmlResponse.text();
    const searchJs = await searchResponse.text();
    if (!html.includes('<script src="search.js"></script>') || !html.includes('data:image/png;base64,') || !searchJs.includes("EverythingHubSearch")) {
      return Response.json({error:"Release source failed homepage/search preflight",sha},{status:422});
    }
    const searchUpload = await fetch(projectUrl+"/storage/v1/object/"+bucket+"/search.js",{method:"POST",headers:{...headers,"content-type":"application/javascript; charset=utf-8","x-upsert":"true","cache-control":"0"},body:searchJs});
    if (!searchUpload.ok) return Response.json({error:"Search client upload failed",status:searchUpload.status,detail:(await searchUpload.text()).slice(0,800)},{status:502});
    const upload = await fetch(projectUrl+"/storage/v1/object/"+bucket+"/index.html",{method:"POST",headers:{...headers,"content-type":"text/html; charset=utf-8","x-upsert":"true","cache-control":"0"},body:html});
    if (!upload.ok) return Response.json({error:"Homepage upload failed",status:upload.status,detail:(await upload.text()).slice(0,800)},{status:502});
    const publicUrl=projectUrl+"/storage/v1/object/public/"+bucket+"/index.html";
    const searchUrl=projectUrl+"/storage/v1/object/public/"+bucket+"/search.js";
    return Response.json({status:"published",url:publicUrl,searchUrl,bytes:new TextEncoder().encode(html).length,searchBytes:new TextEncoder().encode(searchJs).length,contentType:"text/html; charset=utf-8",searchContentType:"application/javascript; charset=utf-8",commitSha:sha,publisher:{repository:publisherClaims.repository,event:publisherClaims.event_name,workflow:publisherClaims.workflow_ref}});
  } catch (e) {
    return Response.json({error:"Publisher request failed",detail:e instanceof Error?e.message:String(e)},{status:502});
  }
});