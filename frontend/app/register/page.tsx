"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

export default function RegisterPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [generatedUID, setGeneratedUID] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const onSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    setGeneratedUID(null);

    const usernameValue = username.trim();
    if (!usernameValue) {
      setError("Username is required.");
      return;
    }
    if (!/^[A-Za-z0-9 ]{3,32}$/.test(usernameValue)) {
      setError("Username must be 3-32 chars and only alphanumeric + space.");
      return;
    }
    if (!password.trim()) {
      setError("Password is required.");
      return;
    }
    if (password.trim().length < 6) {
      setError("Password must be at least 6 characters.");
      return;
    }

    setIsSubmitting(true);
    try {
      const response = await fetch("/api/auth/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: usernameValue, password }),
      });
      const data = (await response.json()) as { error?: string; uid?: string };
      if (!response.ok) {
        setError(data.error ?? "Registration failed.");
        return;
      }

      if (data.uid) {
        setGeneratedUID(data.uid);
        setTimeout(() => {
          router.push("/login");
        }, 1500);
      }
    } catch {
      setError("Unable to reach auth service.");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <main className="min-h-screen bg-white text-blueprint">
      <div className="mx-auto flex min-h-screen w-full max-w-xl items-center justify-center px-6 py-8 fade-in">
        <form
          onSubmit={onSubmit}
          className="w-full space-y-5 border border-dashed border-[var(--blueprint-border)] p-6"
        >
          <h1 className="text-xl text-blueprint">Register Access</h1>

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
            <p className="mt-1 font-mono text-xs text-blueprint/40">
              UID will be generated automatically - format: YY-Name-NNNN
              <br />
              e.g. <span className="text-blueprint/60">25-JaneDoe-0001</span>
            </p>
          </div>

          <div className="space-y-2">
            <label htmlFor="password" className="font-mono text-xs tracking-widest text-blueprint/60">
              PASSWORD
            </label>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="w-full border border-dashed border-[var(--blueprint-border)] px-3 py-2 font-mono text-sm outline-none focus:border-blueprint"
            />
          </div>

          {generatedUID ? (
            <div className="border border-dashed border-blueprint p-3 font-mono">
              <p className="mb-1 text-xs uppercase tracking-widest text-blueprint/50">
                Your Employee UID
              </p>
              <p className="text-sm font-bold tracking-wider text-blueprint">{generatedUID}</p>
              <p className="mt-2 text-xs text-blueprint/40">
                Save this. You will need it to identify yourself internally.
              </p>
            </div>
          ) : null}

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
            {isSubmitting ? "REGISTERING..." : "REGISTER"}
          </button>

          <Link
            href="/login"
            className="block font-mono text-xs text-blueprint/60 transition-colors hover:text-blueprint"
          >
            Already have access? Login →
          </Link>
        </form>
      </div>
    </main>
  );
}
