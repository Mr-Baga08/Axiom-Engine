import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { mockUsers } from "@/lib/auth/mock-store";

export async function POST(req: Request) {
  const payload = (await req
    .json()
    .catch(() => ({}))) as {
    username?: string;
    password?: string;
  };
  const { username, password } = payload;

  const user = mockUsers.find(
    (u) => u.username === username && u.passwordHash === password,
  );

  if (!user) {
    return NextResponse.json(
      { error: "Invalid username or password." },
      { status: 401 },
    );
  }

  (await cookies()).set(
    "mock_session",
    JSON.stringify({ uid: user.uid, username: user.username }),
    {
      httpOnly: true,
      path: "/",
      maxAge: 60 * 60 * 8,
    },
  );

  return NextResponse.json({ uid: user.uid, username: user.username });
}
