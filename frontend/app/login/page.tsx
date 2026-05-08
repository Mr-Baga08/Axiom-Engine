"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const onSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);

    if (!username.trim() || !password.trim()) {
      setError("Username and password are required.");
      return;
    }

    setIsSubmitting(true);
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const data = (await response.json()) as { error?: string };
      if (!response.ok) {
        setError(data.error ?? "Authentication failed.");
        return;
      }
      router.push("/");
    } catch {
      setError("Unable to reach auth service.");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <main className="min-h-screen bg-white text-blueprint">
      <section className="mx-auto grid min-h-screen max-w-6xl grid-cols-1 lg:grid-cols-2">
        <div className="relative hidden border-r border-dashed border-[var(--blueprint-border)] lg:block">
          <svg
            viewBox="0 0 400 400"
            className="h-full w-full opacity-20"
            xmlns="http://www.w3.org/2000/svg"
          >
            <defs>
              <pattern id="grid" width="20" height="20" patternUnits="userSpaceOnUse">
                <path
                  d="M 20 0 L 0 0 0 20"
                  fill="none"
                  stroke="var(--blueprint)"
                  strokeWidth="0.5"
                />
              </pattern>
            </defs>
            <rect width="400" height="400" fill="url(#grid)" />
            <circle
              cx="200"
              cy="200"
              r="80"
              fill="none"
              stroke="var(--blueprint)"
              strokeWidth="1"
              strokeDasharray="4 4"
            />
            <circle
              cx="200"
              cy="200"
              r="40"
              fill="none"
              stroke="var(--blueprint)"
              strokeWidth="0.5"
            />
            <line x1="120" y1="200" x2="280" y2="200" stroke="var(--blueprint)" strokeWidth="0.5" />
            <line x1="200" y1="120" x2="200" y2="280" stroke="var(--blueprint)" strokeWidth="0.5" />
          </svg>
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
            <div className="text-center font-mono text-xs tracking-widest text-blueprint/70">
              <p>AXIOM ENGINE</p>
              <p>──────────────────</p>
              <p>INTERNAL ACCESS ONLY</p>
              <p>REV 1.0.0</p>
            </div>
          </div>
        </div>

        <div className="flex items-center justify-center px-6 py-8 fade-in">
          <form
            onSubmit={onSubmit}
            className="w-full max-w-md space-y-5 border border-dashed border-[var(--blueprint-border)] p-6"
          >
            <h1 className="text-xl text-blueprint">Access Terminal</h1>

            <div className="space-y-2">
              <label htmlFor="username" className="font-mono text-xs tracking-widest text-blueprint/60">
                USERNAME
              </label>
              <input
                id="username"
                name="username"
                type="text"
                autoComplete="username"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                className="w-full border border-dashed border-[var(--blueprint-border)] px-3 py-2 font-mono text-sm outline-none focus:border-blueprint"
              />
            </div>

            <div className="space-y-2">
              <label htmlFor="password" className="font-mono text-xs tracking-widest text-blueprint/60">
                PASSWORD
              </label>
              <input
                id="password"
                name="password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                className="w-full border border-dashed border-[var(--blueprint-border)] px-3 py-2 font-mono text-sm outline-none focus:border-blueprint"
              />
            </div>

            {error ? (
              <p className="border border-dashed border-red-300 px-3 py-2 font-mono text-xs text-red-500">
                ✕ {error}
              </p>
            ) : null}

            <button
              type="submit"
              disabled={isSubmitting}
              className="w-full border border-dashed border-blueprint px-4 py-2 font-mono text-xs tracking-widest text-blueprint transition-colors hover:bg-blueprint hover:text-white disabled:cursor-not-allowed disabled:opacity-60"
            >
              {isSubmitting ? "AUTHENTICATING..." : "AUTHENTICATE"}
            </button>

            <Link
              href="/register"
              className="block font-mono text-xs text-blueprint/60 transition-colors hover:text-blueprint"
            >
              No account? Register →
            </Link>
          </form>
        </div>
      </section>
    </main>
  );
}
