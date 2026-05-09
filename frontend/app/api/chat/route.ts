// Converts the Python API's non-streaming JSON response into the Vercel AI
// data-stream format consumed by @ai-sdk/react useChat.
//
// Verified contract (backend-python/api/main.py + agent.py):
//   POST /api/chat  { message: string }   ← single string, not messages array
//   → { answer: string, tool_trace: [{round, tool, input, output}], ... }
//
// Chart payload is in tool_trace[].output (JSON after Phase 6 fix):
//   { type, title, data, xKey, yKeys }   ← "type" not "chartType"

function writeText(controller: ReadableStreamDefaultController, text: string) {
  controller.enqueue(new TextEncoder().encode(`0:${JSON.stringify(text)}\n`));
}

function writeToolCall(
  controller: ReadableStreamDefaultController,
  toolCallId: string,
  toolName: string,
  args: unknown
) {
  const payload = JSON.stringify({ toolCallId, toolName, args });
  controller.enqueue(new TextEncoder().encode(`9:${payload}\n`));
}

export async function POST(req: Request) {
  const payload = (await req.json().catch(() => ({ messages: [] }))) as {
    messages: Array<{
      content?: string;
      parts?: Array<{ type: string; text?: string }>;
    }>;
  };

  // Extract the last user message text from the AI SDK messages array
  const lastMsg = payload.messages.at(-1);
  const messageText =
    lastMsg?.content ??
    lastMsg?.parts
      ?.filter((p) => p.type === "text")
      .map((p) => p.text ?? "")
      .join("") ??
    "";

  if (!messageText.trim()) {
    return new Response("empty message", { status: 400 });
  }

  const { cookies } = await import("next/headers");
  const apiToken = (await cookies()).get("api_token")?.value;

  const upstreamRes = await fetch(
    `${process.env.BACKEND_URL ?? "http://localhost:8000"}/api/chat`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(apiToken ? { Authorization: `Bearer ${apiToken}` } : {}),
      },
      // Python ChatRequest expects { message: string }, not messages array
      body: JSON.stringify({ message: messageText }),
    }
  ).catch(() => null);

  if (!upstreamRes) {
    return new Response("Backend unavailable", { status: 502 });
  }

  if (!upstreamRes.ok) {
    const status = upstreamRes.status === 401 ? 401 : 502;
    return new Response(`Backend error ${upstreamRes.status}`, { status });
  }

  const data = (await upstreamRes.json()) as {
    answer: string;
    tool_trace: Array<{
      round: number;
      tool: string;
      input: Record<string, string>;
      output: string;
    }>;
    total_tokens?: number;
    cost_usd?: number;
    trace_id?: string;
  };

  const stream = new ReadableStream({
    start(controller) {
      for (const entry of data.tool_trace ?? []) {
        const id = crypto.randomUUID();

        // generate_chart: output is a JSON chart spec { type, title, data, xKey, yKeys }.
        // Pass it directly as args so InsightsChart receives the correct shape.
        let args: unknown = entry.input;
        if (entry.tool === "generate_chart") {
          try {
            args = JSON.parse(entry.output);
          } catch {
            // output still in old str() repr format — pass raw input as fallback
            args = entry.input;
          }
        }

        writeToolCall(controller, id, entry.tool, args);
      }

      writeText(controller, data.answer ?? "");
      controller.close();
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "X-Vercel-AI-Data-Stream": "v1",
    },
  });
}
