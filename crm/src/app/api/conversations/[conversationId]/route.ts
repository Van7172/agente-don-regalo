import { NextRequest } from "next/server";
import { assertInternalToken, jsonError, jsonOk, readJson } from "@/lib/http";
import * as repo from "@/lib/repository";

type Ctx = { params: Promise<{ conversationId: string }> };

export async function GET(_req: NextRequest, ctx: Ctx) {
  try {
    const { conversationId } = await ctx.params;
    const id = Number(conversationId);
    const conv = await repo.getConversation(id);
    if (!conv) return jsonError("Conversation not found", 404);
    const messages = await repo.getMessages(id);
    return jsonOk({
      conversation: {
        id: conv.id_conversation,
        status: conv.status_conversation,
        mode: conv.mode_conversation,
        bot_active: Boolean(conv.bot_active),
        human_support: Boolean(conv.human_support),
        contact: { wa_id: conv.wa_id, name: conv.nombre_contact },
      },
      messages: messages.map((m) => ({
        id: m.id_message,
        direction: m.direction_message,
        sender_type: m.sender_type,
        role: m.role_message,
        content: m.content_message,
        media_url: m.media_url,
        quoted_text: m.quoted_text,
        wa_message_id: m.wa_message_id,
        created_at: m.fecha_creacion,
      })),
    });
  } catch (err) {
    return jsonError(err instanceof Error ? err.message : String(err), 500);
  }
}

export async function POST(req: NextRequest, ctx: Ctx) {
  const denied = assertInternalToken(req);
  if (denied) return denied;
  try {
    const { conversationId } = await ctx.params;
    const id = Number(conversationId);
    const conv = await repo.getConversation(id);
    if (!conv) return jsonError("Conversation not found", 404);

    const body = await readJson<{
      content: string;
      direction?: "inbound" | "outbound";
      sender_type?: "contact" | "bot" | "agent" | "system";
      role?: "user" | "assistant" | "human" | "system";
      wa_message_id?: string;
      media_url?: string;
    }>(req);

    if (!body.content) return jsonError("content required");

    const messageId = await repo.addMessage({
      conversationId: id,
      direction: body.direction || "outbound",
      senderType: body.sender_type || "bot",
      role: body.role || "assistant",
      content: body.content,
      waMessageId: body.wa_message_id,
      mediaUrl: body.media_url,
    });

    return jsonOk({ ok: true, message_id: messageId });
  } catch (err) {
    return jsonError(err instanceof Error ? err.message : String(err), 500);
  }
}
