/** Auth de usuarios Don Regalo — solo en Node (API routes), no en middleware. */
import { timingSafeEqual } from "crypto";
import { cookies } from "next/headers";
import { query, RowDataPacket } from "./db";
import {
  SESSION_COOKIE,
  SessionUser,
  verifySessionToken,
} from "./session";

export type { SessionUser };
export {
  SESSION_COOKIE,
  createSessionToken,
  sessionCookieOptions,
  verifySessionToken,
  hasValidInternalToken,
  unauthorizedJson,
} from "./session";

interface UsuarioRow extends RowDataPacket {
  id_usuario: number;
  login_usuario: string;
  password_usuario: string;
  nombre_usuario: string;
  apellidos_usuario: string;
  email_usuario: string;
  id_rol: number;
  nombre_rol: string | null;
}

function passwordsMatch(stored: string, provided: string): boolean {
  const a = Buffer.from(String(stored ?? ""), "utf8");
  const b = Buffer.from(String(provided ?? ""), "utf8");
  if (a.length !== b.length) return false;
  try {
    return timingSafeEqual(a, b);
  } catch {
    return false;
  }
}

export async function authenticateUser(
  login: string,
  password: string
): Promise<SessionUser | null> {
  const loginClean = login.trim();
  if (!loginClean || !password) return null;

  const rows = await query<UsuarioRow[]>(
    `SELECT u.id_usuario, u.login_usuario, u.password_usuario,
            u.nombre_usuario, u.apellidos_usuario, u.email_usuario,
            u.id_rol, r.nombre_rol
     FROM usuarios u
     LEFT JOIN roles r ON r.id_rol = u.id_rol
     WHERE u.login_usuario = :login
        OR u.email_usuario = :login
     LIMIT 1`,
    { login: loginClean }
  );

  const row = rows[0];
  if (!row || !passwordsMatch(row.password_usuario, password)) {
    return null;
  }

  return {
    id: row.id_usuario,
    login: row.login_usuario,
    name: `${row.nombre_usuario} ${row.apellidos_usuario}`.trim(),
    email: row.email_usuario,
    roleId: row.id_rol,
    roleName: row.nombre_rol || `rol_${row.id_rol}`,
  };
}

export async function getSessionFromCookies(): Promise<SessionUser | null> {
  const jar = await cookies();
  const token = jar.get(SESSION_COOKIE)?.value;
  if (!token) return null;
  return verifySessionToken(token);
}
