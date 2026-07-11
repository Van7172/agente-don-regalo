import { NextRequest } from "next/server";
import { assertInternalToken, jsonError, jsonOk, readJson } from "@/lib/http";
import * as repo from "@/lib/repository";

export async function GET(req: NextRequest) {
  try {
    const phone = req.nextUrl.searchParams.get("phone") || "";
    if (!phone) return jsonError("phone required");
    const lead = await repo.getLeadByPhone(phone.replace(/\D/g, ""));
    return jsonOk({ ok: true, lead });
  } catch (err) {
    return jsonError(err instanceof Error ? err.message : String(err), 500);
  }
}

export async function POST(req: NextRequest) {
  const denied = assertInternalToken(req);
  if (denied) return denied;
  try {
    const body = await readJson<{
      phone: string;
      name?: string;
      email?: string;
      notes?: string;
      temperatura?: string;
    }>(req);
    const waId = (body.phone || "").replace(/\D/g, "");
    if (!waId) return jsonError("phone required");
    await repo.upsertLead({
      waId,
      name: body.name,
      email: body.email,
      notes: body.notes,
      temperatura: body.temperatura,
    });
    const lead = await repo.getLeadByPhone(waId);
    return jsonOk({ ok: true, lead });
  } catch (err) {
    return jsonError(err instanceof Error ? err.message : String(err), 500);
  }
}
