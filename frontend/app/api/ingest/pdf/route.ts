// Proxy PDF uploads to Python /ingest/pdf.
// Requires an api_token cookie (executive or admin role only).

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";

export async function POST(req: Request) {
  const { cookies } = await import("next/headers");
  const apiToken = (await cookies()).get("api_token")?.value;

  if (!apiToken) {
    return new Response(JSON.stringify({ error: "Not authenticated" }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    });
  }

  // Forward the multipart form directly — do not parse, just proxy
  const formData = await req.formData().catch(() => null);
  if (!formData) {
    return new Response(JSON.stringify({ error: "Invalid form data" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }

  const upstreamRes = await fetch(`${BACKEND_URL}/ingest/pdf`, {
    method: "POST",
    headers: { Authorization: `Bearer ${apiToken}` },
    body: formData,
  }).catch(() => null);

  if (!upstreamRes) {
    return new Response(JSON.stringify({ error: "Backend unavailable" }), {
      status: 502,
      headers: { "Content-Type": "application/json" },
    });
  }

  const body = await upstreamRes.text();
  return new Response(body, {
    status: upstreamRes.status,
    headers: { "Content-Type": "application/json" },
  });
}
