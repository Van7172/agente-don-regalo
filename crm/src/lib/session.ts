/** Sesión JWT — seguro para Edge middleware (sin mysql2). */
import { SignJWT, jwtVerify } from "jose";
import { NextRequest, NextResponse } from "next/server";

export const SESSION_COOKIE = "dr_crm_session";
export const SESSION_DAYS = 7;

export type SessionUser = {
  id: number;
  login: string;
  name: string;
  email: string;
  roleId: number;
  roleName: string;
};

function secretKey() {
  const raw = process.env.SESSION_SECRET || "dev-insecure-session-secret-change-me";
  return new TextEncoder().encode(raw);
}

export async function createSessionToken(user: SessionUser): Promise<string> {
  return new SignJWT({
    id: user.id,
    login: user.login,
    name: user.name,
    email: user.email,
    roleId: user.roleId,
    roleName: user.roleName,
  })
    .setProtectedHeader({ alg: "HS256" })
    .setIssuedAt()
    .setExpirationTime(`${SESSION_DAYS}d`)
    .sign(secretKey());
}

export async function verifySessionToken(token: string): Promise<SessionUser | null> {
  try {
    const { payload } = await jwtVerify(token, secretKey());
    if (!payload.id || !payload.login) return null;
    return {
      id: Number(payload.id),
      login: String(payload.login),
      name: String(payload.name || ""),
      email: String(payload.email || ""),
      roleId: Number(payload.roleId || 0),
      roleName: String(payload.roleName || ""),
    };
  } catch {
    return null;
  }
}

export function sessionCookieOptions(token: string) {
  return {
    name: SESSION_COOKIE,
    value: token,
    httpOnly: true,
    sameSite: "lax" as const,
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: SESSION_DAYS * 24 * 60 * 60,
  };
}

export function hasValidInternalToken(req: NextRequest): boolean {
  const expected = process.env.CRM_INTERNAL_TOKEN;
  if (!expected) return false;
  return req.headers.get("x-crm-token") === expected;
}

export function unauthorizedJson() {
  return NextResponse.json({ ok: false, error: "Unauthorized" }, { status: 401 });
}
