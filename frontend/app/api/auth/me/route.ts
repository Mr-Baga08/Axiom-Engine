import { cookies } from "next/headers";
import { NextResponse } from "next/server";

export async function GET() {
  const raw = (await cookies()).get("mock_session")?.value;
  if (!raw) {
    return NextResponse.json(null);
  }
  try {
    return NextResponse.json(JSON.parse(raw));
  } catch {
    return NextResponse.json(null);
  }
}
