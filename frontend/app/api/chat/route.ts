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
  const { messages } = payload;
  // const lastMessage = messages.at(-1);
  // const lastUserMessage =
  //   lastMessage?.content ??
  //   lastMessage?.parts
  //     ?.filter((part) => part.type === "text")
  //     .map((part) => part.text ?? "")
  //     .join("") ??
  //   "";

  const { cookies } = await import("next/headers");
  const sessionCookie = (await cookies()).get("mock_session")?.value;

  const upstreamRes = await fetch(
    `${process.env.BACKEND_URL ?? "http://localhost:8000"}/api/chat`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(sessionCookie ? { Cookie: `mock_session=${sessionCookie}` } : {}),
      },
      body: JSON.stringify({ messages }),
    }
  );

  if (!upstreamRes.ok || !upstreamRes.body) {
    return new Response("Backend unavailable", { status: 502 });
  }

  const stream = new ReadableStream({
    async start(controller) {
      const reader = upstreamRes.body?.getReader();
      const decoder = new TextDecoder();

      while (reader) {
        const { done, value } = await reader.read();
        if (done) {
          break;
        }

        const raw = decoder.decode(value, { stream: true });
        for (const line of raw.split("\n")) {
          if (!line.trim()) {
            continue;
          }

          if (line.startsWith("TEXT:")) {
            writeText(controller, line.slice(5));
          } else if (line.startsWith("TOOL:")) {
            try {
              const tool = JSON.parse(line.slice(5)) as {
                toolCallId?: string;
                toolName: string;
                args: unknown;
              };
              writeToolCall(
                controller,
                tool.toolCallId ?? crypto.randomUUID(),
                tool.toolName,
                tool.args
              );
            } catch {
              // Skip malformed tool line
            }
          } else {
            writeText(controller, line);
          }
        }
      }
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
