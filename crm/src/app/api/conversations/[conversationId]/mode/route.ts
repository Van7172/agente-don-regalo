import { NextRequest } from "next/server";
import { jsonError, jsonOk, readJson } from "@/lib/http";
import * as repo from "@/lib/repository";

type Ctx = { params: Promise<{ conversationId: string }> };

export async function PATCH(req: NextRequest, ctx: Ctx) {
  try {
    const { conversationId } = await ctx.params;
    const id = Number(conversationId);
    const body = await readJson<{ mode?: "AI" | "HUMAN"; bot_active?: boolean }>(req);

    if (body.mode === "AI" || body.mode === "HUMAN") {
      await repo.setMode(id, body.mode);
    }
    if (typeof body.bot_active === "boolean") {
      await repo.setBotActive(id, body.bot_active);
    }

    const conv = await repo.getConversation(id);
    if (!conv) return jsonError("Conversation not found", 404);
    return jsonOk({
      ok: true,
      conversation: {
        id: conv.id_conversation,
        mode: conv.mode_conversation,
        bot_active: Boolean(conv.bot_active),
        human_support: Boolean(conv.human_support),
      },
    });
  } catch (err) {
    return jsonError(err instanceof Error ? err.message : String(err), 500);
  }
}
