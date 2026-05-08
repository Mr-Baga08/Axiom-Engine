"use client";

import { useEffect, useState } from "react";

export interface Session {
  uid: string;
  username: string;
}

export function useSession() {
  const [session, setSession] = useState<Session | null | "loading">("loading");

  useEffect(() => {
    fetch("/api/auth/me")
      .then((r) => r.json())
      .then((data: Session | null) => setSession(data ?? null))
      .catch(() => setSession(null));
  }, []);

  async function logout() {
    await fetch("/api/auth/logout", { method: "POST" });
    setSession(null);
    window.location.href = "/login";
  }

  return { session, logout };
}
