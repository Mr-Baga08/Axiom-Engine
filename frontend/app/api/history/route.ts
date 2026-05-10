// Proxy conversation history to Python /history (Redis-backed, per-user).

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";

async function getApiToken(): Promise<string | undefined> {
  const { cookies } = await import("next/headers");
  return (await cookies()).get("api_token")?.value;
}

export async function GET() {
  const apiToken = await getApiToken();
  if (!apiToken) {
    return Response.json({ history: [] });
  }

  try {
    const res = await fetch(`${BACKEND_URL}/history`, {
      headers: { Authorization: `Bearer ${apiToken}` },
    });
    if (!res.ok) {
      return Response.json({ history: [] });
    }
    return Response.json(await res.json());
  } catch {
    return Response.json({ history: [] });
  }
}

export async function DELETE() {
  const apiToken = await getApiToken();
  if (!apiToken) {
    return Response.json({ cleared: false });
  }

  try {
    await fetch(`${BACKEND_URL}/history`, {
      method: "DELETE",
      headers: { Authorization: `Bearer ${apiToken}` },
    });
  } catch {
    // Redis down — swallow
  }
  return Response.json({ cleared: true });
}
