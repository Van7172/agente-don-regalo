import { NextRequest } from "next/server";
import { assertInternalToken, jsonError, jsonOk, readJson } from "@/lib/http";
import * as repo from "@/lib/repository";

export async function GET(req: NextRequest) {
  try {
    const key = req.nextUrl.searchParams.get("key");
    if (key) {
      const value = await repo.getSetting(key);
      return jsonOk({ ok: true, key, value });
    }
    const paused = await repo.getSetting("paused");
    return jsonOk({
      ok: true,
      settings: {
        paused: paused === "1",
      },
    });
  } catch (err) {
    return jsonError(err instanceof Error ? err.message : String(err), 500);
  }
}

export async function PUT(req: NextRequest) {
  const denied = assertInternalToken(req);
  if (denied) return denied;
  try {
    const body = await readJson<Record<string, string | boolean | number>>(req);
    for (const [key, value] of Object.entries(body)) {
      const stored =
        typeof value === "boolean" ? (value ? "1" : "0") : String(value);
      await repo.setSetting(key, stored);
    }
    return jsonOk({ ok: true });
  } catch (err) {
    return jsonError(err instanceof Error ? err.message : String(err), 500);
  }
}
