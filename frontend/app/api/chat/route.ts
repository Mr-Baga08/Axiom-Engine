// Next.js API route — bridges useChat() (ai@6 / @ai-sdk/react@3) to the backend.
//
// ai@6 uses the "UI Message Stream" protocol, NOT the old data stream format.
// Correct SSE format: data: {"type":"text-delta","delta":"..."}\n\n
// Correct headers:    x-vercel-ai-ui-message-stream: v1
//
// Primary path  : Go SSE gateway (:8080/stream) → Python /internal/query
// Fallback path : Python /api/chat (JSON response)

import {
  createUIMessageStream,
  createUIMessageStreamResponse,
} from "ai";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";
const GO_SSE_URL  = process.env.GO_SSE_URL  ?? "http://localhost:8080";

// ── Helpers ────────────────────────────────────────────────────────────────

async function getApiToken(): Promise<string | undefined> {
  const { cookies } = await import("next/headers");
  return (await cookies()).get("api_token")?.value;
}

function extractMessage(payload: {
  messages?: Array<{
    content?: string;
    parts?: Array<{ type: string; text?: string }>;
  }>;
}): string {
  const last = payload.messages?.at(-1);
  return (
    last?.content ??
    last?.parts
      ?.filter((p) => p.type === "text")
      .map((p) => p.text ?? "")
      .join("") ??
    ""
  );
}

// ── Shared response builder ────────────────────────────────────────────────

interface ToolEntry { tool: string; args: unknown; output?: unknown }

function buildUIMessageStreamResponse(toolCalls: ToolEntry[], answer: string): Response {
  const stream = createUIMessageStream({
    execute: ({ writer }) => {
      // Emit each tool call with its input and output
      // Exact field requirements validated against ai@6 at build time:
      //   tool-input-available : { toolCallId, toolName, input }
      //   tool-output-available: { toolCallId, output }          (no toolName)
      //   text-delta           : { id, delta }
      //   finish-step          : {}
      toolCalls.forEach((tc, i) => {
        const tcId = `tc-${i}-${tc.tool}`;
        writer.write({ type: "tool-input-available", toolCallId: tcId, toolName: tc.tool, input: tc.args ?? {} });
        if (tc.output !== undefined) {
          writer.write({ type: "tool-output-available", toolCallId: tcId, output: tc.output });
        }
      });

      // text-start must precede text-delta so the SDK creates the text part;
      // without it text-delta has nowhere to accumulate and the part is never
      // added to messages[].parts, so the answer never renders.
      if (answer) {
        writer.write({ type: "text-start", id: "answer" });
        writer.write({ type: "text-delta", id: "answer", delta: answer });
        writer.write({ type: "text-end",   id: "answer" });
      }

      // Finish — commits the message into messages[]
      writer.write({ type: "finish-step" });
    },
  });

  return createUIMessageStreamResponse({ stream });
}

// ── Primary: Go SSE gateway ────────────────────────────────────────────────

async function callViaGo(message: string, apiToken: string | undefined): Promise<Response> {
  const goRes = await fetch(`${GO_SSE_URL}/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(apiToken ? { Authorization: `Bearer ${apiToken}` } : {}),
    },
    body: JSON.stringify({ message }),
  });

  if (!goRes.ok) throw new Error(`Go gateway ${goRes.status}`);

  const raw = await goRes.text();

  const toolCalls: ToolEntry[] = [];
  let answer = "";

  for (const line of raw.split("\n")) {
    if (!line.startsWith("data: ")) continue;
    const payload = line.slice(6).trim();
    if (!payload || payload === "{}" || payload === "[DONE]") continue;

    try {
      const evt = JSON.parse(payload) as Record<string, unknown>;

      if (evt.type === "tool_call") {
        toolCalls.push({ tool: evt.tool as string, args: evt.args });
      } else if (evt.type === "token") {
        answer += (evt.content as string) ?? "";
      } else if (evt.type === "done") {
        answer = (evt.answer as string) ?? answer;
        const trace = evt.tool_trace as Array<{ tool: string; input: unknown; output: string }> | undefined;
        if (trace?.length) {
          toolCalls.length = 0;
          for (const t of trace) {
            let output: unknown = t.output;
            try { output = JSON.parse(t.output); } catch { /* keep string */ }
            toolCalls.push({ tool: t.tool, args: t.input, output });
          }
        }
      }
    } catch {
      continue;
    }
  }

  return buildUIMessageStreamResponse(toolCalls, answer);
}

// ── Fallback: Python JSON endpoint ─────────────────────────────────────────

async function callViaPython(message: string, apiToken: string | undefined): Promise<Response> {
  const res = await fetch(`${BACKEND_URL}/api/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(apiToken ? { Authorization: `Bearer ${apiToken}` } : {}),
    },
    body: JSON.stringify({ message }),
  });

  if (!res.ok) return new Response(`Backend error ${res.status}`, { status: res.status === 401 ? 401 : 502 });

  const data = (await res.json()) as {
    answer: string;
    tool_trace: Array<{ tool: string; input: unknown; output: string }>;
  };

  const toolCalls: ToolEntry[] = (data.tool_trace ?? []).map((t) => {
    let output: unknown = t.output;
    try { output = JSON.parse(t.output); } catch { /* keep string */ }
    return { tool: t.tool, args: t.input, output };
  });

  return buildUIMessageStreamResponse(toolCalls, data.answer ?? "");
}

// ── Handler ────────────────────────────────────────────────────────────────

export async function POST(req: Request) {
  const payload = (await req.json().catch(() => ({ messages: [] }))) as {
    messages: Array<{ content?: string; parts?: Array<{ type: string; text?: string }> }>;
  };

  const messageText = extractMessage(payload);
  if (!messageText.trim()) return new Response("empty message", { status: 400 });

  const apiToken = await getApiToken();

  try {
    return await callViaGo(messageText, apiToken);
  } catch {
    try {
      return await callViaPython(messageText, apiToken);
    } catch {
      return new Response("All backends unavailable", { status: 502 });
    }
  }
}
