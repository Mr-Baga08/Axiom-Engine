import { NextResponse } from "next/server";
import {
  generateUID,
  incrementCounter,
  mockUsers,
} from "@/lib/auth/mock-store";

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

  if (mockUsers.find((u) => u.username === username)) {
    return NextResponse.json(
      { error: "Username already taken." },
      { status: 409 }
    );
  }

  const uid = generateUID(username);
  incrementCounter();

  const user = {
    uid,
    username,
    passwordHash: password,
    registeredAt: new Date().toISOString(),
  };
  mockUsers.push(user);

  return NextResponse.json({ uid, username }, { status: 201 });
}
