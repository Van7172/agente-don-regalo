import { NextRequest } from "next/server";
import { assertInternalToken, jsonError, jsonOk } from "@/lib/http";
import * as repo from "@/lib/repository";

export async function GET(req: NextRequest) {
  const denied = assertInternalToken(req);
  if (denied) return denied;
  try {
    const minSec = Number(req.nextUrl.searchParams.get("min_sec") || 180);
    const maxSec = Number(req.nextUrl.searchParams.get("max_sec") || 7200);
    const rows = await repo.getUnansweredConversations(minSec, maxSec);
    return jsonOk({
      ok: true,
      data: rows.map((r) => ({
        id: r.id_conversation,
        phone: r.phone,
        name: r.name,
        last_role: r.last_role,
        last_at: r.last_at,
      })),
    });
  } catch (err) {
    return jsonError(err instanceof Error ? err.message : String(err), 500);
  }
}
