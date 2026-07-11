import { NextRequest } from "next/server";
import { assertInternalToken, jsonError, jsonOk, readJson } from "@/lib/http";
import * as repo from "@/lib/repository";

type Ctx = { params: Promise<{ phone: string }> };

export async function GET(_req: NextRequest, ctx: Ctx) {
  try {
    const { phone } = await ctx.params;
    const waId = phone.replace(/\D/g, "");
    const memory = await repo.getMemory(waId);
    return jsonOk({ ok: true, memory });
  } catch (err) {
    return jsonError(err instanceof Error ? err.message : String(err), 500);
  }
}

export async function PUT(req: NextRequest, ctx: Ctx) {
  const denied = assertInternalToken(req);
  if (denied) return denied;
  try {
    const { phone } = await ctx.params;
    const waId = phone.replace(/\D/g, "");
    const body = await readJson<Record<string, unknown>>(req);
    await repo.upsertMemory(waId, body);
    const memory = await repo.getMemory(waId);
    return jsonOk({ ok: true, memory });
  } catch (err) {
    return jsonError(err instanceof Error ? err.message : String(err), 500);
  }
}
