import { jsonError, jsonOk } from "@/lib/http";
import { ensureTenantId } from "@/lib/repository";

export async function GET() {
  try {
    const tenantId = await ensureTenantId();
    return jsonOk({
      ok: true,
      service: "don-regalo-crm",
      tenant_id: tenantId,
      ts: new Date().toISOString(),
    });
  } catch (err) {
    return jsonError(err instanceof Error ? err.message : String(err), 500);
  }
}
