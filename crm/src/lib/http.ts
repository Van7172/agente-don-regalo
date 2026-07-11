import { NextRequest, NextResponse } from "next/server";

export function jsonOk(data: unknown, status = 200) {
  return NextResponse.json(data, { status });
}

export function jsonError(message: string, status = 400) {
  return NextResponse.json({ ok: false, error: message }, { status });
}

/** Valida token interno agent↔CRM si está configurado. */
export function assertInternalToken(req: NextRequest): NextResponse | null {
  const expected = process.env.CRM_INTERNAL_TOKEN;
  if (!expected) return null;
  const got = req.headers.get("x-crm-token") || "";
  if (got !== expected) {
    return jsonError("Unauthorized", 401);
  }
  return null;
}

export async function readJson<T = Record<string, unknown>>(req: NextRequest): Promise<T> {
  return (await req.json()) as T;
}
