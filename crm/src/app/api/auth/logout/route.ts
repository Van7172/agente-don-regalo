import { SESSION_COOKIE } from "@/lib/auth";
import { jsonOk } from "@/lib/http";

export async function POST() {
  const res = jsonOk({ ok: true });
  res.cookies.set({
    name: SESSION_COOKIE,
    value: "",
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: 0,
  });
  return res;
}
