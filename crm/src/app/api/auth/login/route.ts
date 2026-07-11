import { NextRequest } from "next/server";
import {
  authenticateUser,
  createSessionToken,
  sessionCookieOptions,
} from "@/lib/auth";
import { jsonError, jsonOk, readJson } from "@/lib/http";

export async function POST(req: NextRequest) {
  try {
    const body = await readJson<{ login?: string; password?: string }>(req);
    const login = (body.login || "").trim();
    const password = body.password || "";

    if (!login || !password) {
      return jsonError("Usuario y contraseña son obligatorios", 400);
    }

    const user = await authenticateUser(login, password);
    if (!user) {
      return jsonError("Credenciales incorrectas", 401);
    }

    const token = await createSessionToken(user);
    const res = jsonOk({
      ok: true,
      user: {
        id: user.id,
        login: user.login,
        name: user.name,
        email: user.email,
        role: user.roleName,
      },
    });
    res.cookies.set(sessionCookieOptions(token));
    return res;
  } catch (err) {
    return jsonError(err instanceof Error ? err.message : String(err), 500);
  }
}
