import { NextRequest } from "next/server";
import { assertInternalToken, jsonError, jsonOk, readJson } from "@/lib/http";
import * as repo from "@/lib/repository";

export async function GET() {
  try {
    const rows = await repo.listConversations(80);
    return jsonOk({
      data: rows.map((c) => ({
        id: c.id_conversation,
        status: c.status_conversation,
        mode: c.mode_conversation,
        bot_active: Boolean(c.bot_active),
        human_support: Boolean(c.human_support),
        last_message_at: c.last_message_at,
        contact: { wa_id: c.wa_id, name: c.nombre_contact },
        last_message: (c.last_message_preview || "").slice(0, 120),
      })),
    });
  } catch (err) {
    return jsonError(err instanceof Error ? err.message : String(err), 500);
  }
}

/** Agente: asegura conversación + inserta mensaje inbound. */
export async function POST(req: NextRequest) {
  const denied = assertInternalToken(req);
  if (denied) return denied;
  try {
    const body = await readJson<{
      wa_id: string;
      name?: string;
      content?: string;
      wa_message_id?: string;
      media_url?: string;
      quoted_text?: string;
      direction?: "inbound" | "outbound";
      sender_type?: "contact" | "bot" | "agent" | "system";
      role?: "user" | "assistant" | "human" | "system";
    }>(req);

    if (!body.wa_id) return jsonError("wa_id required");

    const ids = await repo.ensureInboundConversation(body.wa_id, body.name || "");
    let messageId: number | null = null;
    if (body.content) {
      messageId = await repo.addMessage({
        conversationId: ids.conversationId,
        direction: body.direction || "inbound",
        senderType: body.sender_type || "contact",
        role: body.role || "user",
        content: body.content,
        waMessageId: body.wa_message_id,
        mediaUrl: body.media_url,
        quotedText: body.quoted_text,
      });
    }

    const conv = await repo.getConversation(ids.conversationId);
    return jsonOk({
      ok: true,
      tenant_id: ids.tenantId,
      contact_id: ids.contactId,
      conversation_id: ids.conversationId,
      message_id: messageId,
      conversation: conv
        ? {
            id: conv.id_conversation,
            mode: conv.mode_conversation,
            bot_active: Boolean(conv.bot_active),
            human_support: Boolean(conv.human_support),
          }
        : null,
    });
  } catch (err) {
    return jsonError(err instanceof Error ? err.message : String(err), 500);
  }
}
