import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { mockUsers } from "@/lib/auth/mock-store";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";

export async function POST(req: Request) {
  const payload = (await req.json().catch(() => ({}))) as {
    username?: string;
    password?: string;
  };
  const { username, password } = payload;

  if (!username?.trim() || !password?.trim()) {
    return NextResponse.json(
      { error: "Username and password are required." },
      { status: 400 }
    );
  }

  // Try Python backend first so we get a real JWT for API calls
  let apiToken: string | null = null;
  let resolvedUid: string | undefined;

  try {
    const pyRes = await fetch(`${BACKEND_URL}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (pyRes.ok) {
      const pyData = (await pyRes.json()) as {
        access_token: string;
        uid: string;
      };
      apiToken = pyData.access_token;
      resolvedUid = pyData.uid;
    }
  } catch {
    // Python unavailable — fall back to mock-only auth
  }

  if (apiToken && resolvedUid) {
    const jar = await cookies();
    jar.set("api_token", apiToken, {
      httpOnly: true,
      path: "/",
      maxAge: 60 * 60 * 8, // match mock_session so both expire together
      sameSite: "strict",
    });
    jar.set("mock_session", JSON.stringify({ uid: resolvedUid, username }), {
      httpOnly: true,
      path: "/",
      maxAge: 60 * 60 * 8,
    });
    return NextResponse.json({ uid: resolvedUid, username });
  }

  // Fallback: accept users registered locally via /api/auth/register
  const user = mockUsers.find(
    (u) => u.username === username && u.passwordHash === password
  );
  if (!user) {
    return NextResponse.json(
      { error: "Invalid username or password." },
      { status: 401 }
    );
  }

  const cookieStore = await cookies();
  cookieStore.set(
    "mock_session",
    JSON.stringify({ uid: user.uid, username: user.username }),
    { httpOnly: true, path: "/", maxAge: 60 * 60 * 8 }
  );
  return NextResponse.json({ uid: user.uid, username: user.username });
}
