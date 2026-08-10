import { HttpResponse, type HttpResponseResolver } from "msw";



/**
 * Mocks an agent chat endpoint.
 *
 * Request  (POST /api/chat):
 *   {
 *     messages: [{ role, content }, ...],
 *     context?: { page, artifactTitle? }
 *   }
 *
 * Response: `text/event-stream` of NDJSON-shaped events:
 *   { type: "tool_call", name, args }      (optional, 0+)
 *   { type: "delta",     text }            (repeated — token stream)
 *   { type: "cite",      artifactId }      (optional, 0+)
 *   { type: "done" }
 */
export const chatHandler: HttpResponseResolver = async ({ request }) => {
  const body = (await request.json().catch(() => null)) as
    | {
        messages: Array<{ role: string; content: string }>;
        context?: { page?: string; artifactTitle?: string };
      }
    | null;

  const last =
    body?.messages?.[body.messages.length - 1]?.content ?? "(empty prompt)";
  const context = body?.context;
  const prompt = last.toLowerCase();

  // Canned demo replies based on the command keyword.
  const reply = buildReply(prompt, context);

  const encoder = new TextEncoder();
  let closed = false;

  const stream = new ReadableStream({
    async start(controller) {
      const send = (e: unknown) => {
        if (closed) return;
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(e)}\n\n`));
      };

      // Simulate an upfront tool call if the user asked for critique/summarize
      if (reply.toolCall) {
        await sleep(200);
        send({ type: "tool_call", name: reply.toolCall.name, args: reply.toolCall.args });
      }

      // Stream the body as word-chunks (25-45ms apart) to imitate frontier LLM cadence.
      const words = reply.text.split(/(\s+)/);
      for (const w of words) {
        if (closed) break;
        send({ type: "delta", text: w });
        await sleep(25 + Math.random() * 20);
      }

      // Optional citation(s)
      for (const artifactId of reply.cites ?? []) {
        send({ type: "cite", artifactId });
      }

      send({ type: "done" });
      controller.close();
    },
    cancel() {
      closed = true;
    },
  });

  return new HttpResponse(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
    },
  });
};

function sleep(ms: number) {
  return new Promise((r) => setTimeout(r, ms));
}

// ---- scripted replies ----

function buildReply(
  prompt: string,
  context: { page?: string; artifactTitle?: string } | undefined,
): { text: string; toolCall?: { name: string; args: Record<string, unknown> }; cites?: string[] } {
  const where = context?.page
    ? ` while reviewing **${context.page}**${
        context?.artifactTitle ? ` → *${context.artifactTitle}*` : ""
      }`
    : "";

  if (prompt.startsWith("/summarize")) {
    return {
      toolCall: {
        name: "read_artifact",
        args: { title: context?.artifactTitle ?? "selected artifact" },
      },
      text: `**Summary**${where}:\n\n- The main flow is idempotent replay via a client-supplied \`Idempotency-Key\`.\n- A unique index on \`(batch_id, idempotency_key)\` enforces dedup at the DB layer.\n- A rollback plan is on file and disables the feature flag before reverting the image.\n\nLet me know if you want a critique or want to drill into any section.`,
      cites: context?.artifactTitle ? ["art_adr_1"] : undefined,
    };
  }

  if (prompt.startsWith("/critique")) {
    return {
      toolCall: {
        name: "read_artifact",
        args: { title: context?.artifactTitle ?? "selected artifact" },
      },
      text: `**Review**${where}:\n\n1. **Blocking** — the replay endpoint returns 200 on \`already_enqueued\`, but 409 only on \`succeeded\`. That's fine, but document it. A distracted client may retry on 200 thinking it failed.\n2. **Suggestion** — back-fill strategy isn't called out for the new \`replays.requested_by\` column. Worth an ADR note.\n3. **Nit** — consider an \`x-request-id\` header for traceability.`,
    };
  }

  if (prompt.startsWith("/edit")) {
    return {
      text: `**Proposed edit**${where}:\n\n\`\`\`diff\n- then: the batch is re-enqueued with idempotency key preserved\n+ then: the batch is re-enqueued; the Idempotency-Key is echoed in the 202 body\n\`\`\`\n\nAccept this edit? Reply /apply.`,
    };
  }

  if (prompt.startsWith("/explain")) {
    return {
      text: `Plain-English version${where}:\n\nThe client sends a unique key with each replay request. If the server has already seen that key, it short-circuits — so clicking Replay twice does the right thing.`,
    };
  }

  if (prompt.startsWith("/apply")) {
    return {
      toolCall: { name: "update_artifact", args: { dry_run: true } },
      text: `Applied the suggested edit (dry-run). When the orchestrator wires in Chunk 16 this will flow through as an \`artifact.updated\` event on the stream.`,
    };
  }

  // Default — echo back something useful
  return {
    text: `Got it${where}. I can run **/summarize**, **/critique**, **/edit**, or **/explain** on the current artifact. Tell me what you want.`,
  };
}
