import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "npm:@supabase/supabase-js@2.49.1";
const bucket = "everythinghub-public-site";
Deno.serve(async (req: Request) => {
  if (req.method !== "GET" && req.method !== "POST") return Response.json({error:"GET or POST only"},{status:405});
  const projectUrl = Deno.env.get("SUPABASE_URL") || "https://vdceayvfgkwpgwixrhwi.supabase.co";
  const key = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";
  if (!key) return Response.json({error:"SUPABASE_SERVICE_ROLE_KEY is not available to this function. Storage upload cannot be authorized."},{status:503});
  const headers = {apikey:key,Authorization:"Bearer "+key};
  const admin = createClient(projectUrl, key, { auth: { persistSession: false, autoRefreshToken: false } });
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
  if (!token) return Response.json({ error: "Sign-in required to publish." }, { status: 401 });
  const { data: authData, error: authError } = await admin.auth.getUser(token);
  if (authError || !authData.user) {
    return Response.json({ error: "A valid signed-in user is required to publish." }, { status: 401 });
  }

  try {
    const list = await fetch(projectUrl+"/storage/v1/bucket",{headers});
    if (!list.ok) return Response.json({error:"Storage bucket listing failed",status:list.status,detail:(await list.text()).slice(0,800)},{status:502});
    const buckets = await list.json();
    if (!Array.isArray(buckets) || !buckets.some((b:any)=>b.name===bucket)) {
      const create = await fetch(projectUrl+"/storage/v1/bucket",{method:"POST",headers:{...headers,"content-type":"application/json"},body:JSON.stringify({name:bucket,public:true,file_size_limit:5242880})});
      if (!create.ok && create.status!==409) return Response.json({error:"Storage bucket creation failed",status:create.status,detail:(await create.text()).slice(0,800)},{status:502});
    }
    const publishRequest = await req.json().catch(() => ({}));
    const sha = typeof publishRequest.sha === "string" && /^[0-9a-f]{40}$/i.test(publishRequest.sha) ? publishRequest.sha : "main";
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
    return Response.json({status:"published",url:publicUrl,searchUrl,bytes:new TextEncoder().encode(html).length,searchBytes:new TextEncoder().encode(searchJs).length,contentType:"text/html; charset=utf-8",searchContentType:"application/javascript; charset=utf-8",commitSha:sha});
  } catch (e) {
    return Response.json({error:"Publisher request failed",detail:e instanceof Error?e.message:String(e)},{status:502});
  }
});