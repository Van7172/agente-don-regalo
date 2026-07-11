import { NextRequest } from "next/server";
import { assertInternalToken, jsonError, jsonOk, readJson } from "@/lib/http";
import * as repo from "@/lib/repository";

export async function GET(req: NextRequest) {
  const denied = assertInternalToken(req);
  if (denied) return denied;
  try {
    const rows = await repo.listPendingOutbox(30);
    return jsonOk({ ok: true, data: rows });
  } catch (err) {
    return jsonError(err instanceof Error ? err.message : String(err), 500);
  }
}

export async function POST(req: NextRequest) {
  try {
    const body = await readJson<{
      conversation_id: number;
      content: string;
      type?: "text" | "image";
      media_path?: string;
    }>(req);

    if (!body.conversation_id || !body.content) {
      return jsonError("conversation_id and content required");
    }
    const conv = await repo.getConversation(body.conversation_id);
    if (!conv) return jsonError("Conversation not found", 404);

    const id = await repo.enqueueOutbox({
      conversationId: body.conversation_id,
      waId: conv.wa_id,
      content: body.content,
      type: body.type,
      mediaPath: body.media_path,
    });

    // Intento inmediato vía agente sandbox (best-effort)
    const agentUrl = process.env.AGENT_BASE_URL;
    const agentToken = process.env.AGENT_INTERNAL_TOKEN;
    if (agentUrl) {
      try {
        const res = await fetch(`${agentUrl.replace(/\/$/, "")}/internal/outbox/send`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...(agentToken ? { "X-Agent-Token": agentToken } : {}),
          },
          body: JSON.stringify({
            outbox_id: id,
            wa_id: conv.wa_id,
            content: body.content,
            conversation_id: body.conversation_id,
          }),
        });
        if (res.ok) {
          await repo.markOutbox(id, "sent");
          await repo.addMessage({
            conversationId: body.conversation_id,
            direction: "outbound",
            senderType: "agent",
            role: "human",
            content: body.content,
          });
          await repo.setMode(body.conversation_id, "HUMAN");
        }
      } catch {
        /* queda pending para poll del agente */
      }
    }

    return jsonOk({ ok: true, outbox_id: id });
  } catch (err) {
    return jsonError(err instanceof Error ? err.message : String(err), 500);
  }
}

export async function PATCH(req: NextRequest) {
  const denied = assertInternalToken(req);
  if (denied) return denied;
  try {
    const body = await readJson<{
      outbox_id: number;
      status: "sent" | "failed";
      error?: string;
    }>(req);
    if (!body.outbox_id || !body.status) return jsonError("outbox_id and status required");
    await repo.markOutbox(body.outbox_id, body.status, body.error);
    return jsonOk({ ok: true });
  } catch (err) {
    return jsonError(err instanceof Error ? err.message : String(err), 500);
  }
}
