import { getSessionFromCookies, unauthorizedJson } from "@/lib/auth";
import { jsonOk } from "@/lib/http";

export async function GET() {
  const user = await getSessionFromCookies();
  if (!user) return unauthorizedJson();
  return jsonOk({
    ok: true,
    user: {
      id: user.id,
      login: user.login,
      name: user.name,
      email: user.email,
      role: user.roleName,
    },
  });
}
