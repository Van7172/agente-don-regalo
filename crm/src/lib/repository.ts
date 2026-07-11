import { execute, query, RowDataPacket } from "./db";

const TENANT_SLUG = () => process.env.CRM_TENANT_SLUG || "don-regalo";

export interface TenantRow extends RowDataPacket {
  id_tenant: number;
  slug_tenant: string;
  nombre_tenant: string;
}

export interface ConversationListRow extends RowDataPacket {
  id_conversation: number;
  status_conversation: string;
  mode_conversation: string;
  bot_active: number;
  human_support: number;
  last_message_at: Date | null;
  wa_id: string;
  nombre_contact: string;
  last_message_preview: string | null;
}

export interface MessageRow extends RowDataPacket {
  id_message: number;
  id_conversation: number;
  direction_message: string;
  sender_type: string;
  role_message: string;
  wa_message_id: string | null;
  content_message: string;
  media_url: string | null;
  quoted_text: string | null;
  fecha_creacion: Date;
}

export async function ensureTenantId(): Promise<number> {
  const rows = await query<TenantRow[]>(
    `SELECT id_tenant, slug_tenant, nombre_tenant FROM crm_tenants WHERE slug_tenant = :slug LIMIT 1`,
    { slug: TENANT_SLUG() }
  );
  if (rows[0]) return rows[0].id_tenant;

  const result = await execute(
    `INSERT INTO crm_tenants (slug_tenant, nombre_tenant, config_tenant)
     VALUES (:slug, :nombre, :config)`,
    {
      slug: TENANT_SLUG(),
      nombre: "Don Regalo",
      config: JSON.stringify({ locale: "es-PE" }),
    }
  );
  return Number(result.insertId);
}

export async function getOrCreateContact(
  tenantId: number,
  waId: string,
  name = ""
): Promise<number> {
  const existing = await query<RowDataPacket[]>(
    `SELECT id_contact FROM crm_contacts WHERE id_tenant = :tenantId AND wa_id = :waId LIMIT 1`,
    { tenantId, waId }
  );
  if (existing[0]) {
    if (name) {
      await execute(
        `UPDATE crm_contacts SET nombre_contact = CASE
           WHEN nombre_contact = '' OR nombre_contact IS NULL THEN :name
           ELSE nombre_contact END
         WHERE id_contact = :id`,
        { name, id: existing[0].id_contact }
      );
    }
    return Number(existing[0].id_contact);
  }
  const result = await execute(
    `INSERT INTO crm_contacts (id_tenant, wa_id, nombre_contact)
     VALUES (:tenantId, :waId, :name)`,
    { tenantId, waId, name }
  );
  return Number(result.insertId);
}

export async function getOrCreateConversation(
  tenantId: number,
  contactId: number
): Promise<number> {
  const existing = await query<RowDataPacket[]>(
    `SELECT id_conversation FROM crm_conversations
     WHERE id_tenant = :tenantId AND id_contact = :contactId AND status_conversation = 'open'
     ORDER BY id_conversation DESC LIMIT 1`,
    { tenantId, contactId }
  );
  if (existing[0]) return Number(existing[0].id_conversation);

  const result = await execute(
    `INSERT INTO crm_conversations (id_tenant, id_contact, status_conversation, mode_conversation, bot_active)
     VALUES (:tenantId, :contactId, 'open', 'AI', 1)`,
    { tenantId, contactId }
  );
  return Number(result.insertId);
}

export async function listConversations(limit = 50): Promise<ConversationListRow[]> {
  const tenantId = await ensureTenantId();
  return query<ConversationListRow[]>(
    `SELECT c.id_conversation, c.status_conversation, c.mode_conversation,
            c.bot_active, c.human_support, c.last_message_at,
            ct.wa_id, ct.nombre_contact,
            (SELECT m.content_message FROM crm_messages m
             WHERE m.id_conversation = c.id_conversation
             ORDER BY m.id_message DESC LIMIT 1) AS last_message_preview
     FROM crm_conversations c
     JOIN crm_contacts ct ON ct.id_contact = c.id_contact
     WHERE c.id_tenant = :tenantId
     ORDER BY COALESCE(c.last_message_at, c.fecha_creacion) DESC
     LIMIT ${Number(limit)}`,
    { tenantId }
  );
}

export async function getConversation(id: number) {
  const rows = await query<ConversationListRow[]>(
    `SELECT c.id_conversation, c.status_conversation, c.mode_conversation,
            c.bot_active, c.human_support, c.last_message_at,
            ct.wa_id, ct.nombre_contact, NULL AS last_message_preview
     FROM crm_conversations c
     JOIN crm_contacts ct ON ct.id_contact = c.id_contact
     WHERE c.id_conversation = :id LIMIT 1`,
    { id }
  );
  return rows[0] || null;
}

export async function getMessages(conversationId: number, limit = 200): Promise<MessageRow[]> {
  return query<MessageRow[]>(
    `SELECT id_message, id_conversation, direction_message, sender_type, role_message,
            wa_message_id, content_message, media_url, quoted_text, fecha_creacion
     FROM crm_messages
     WHERE id_conversation = :conversationId
     ORDER BY id_message ASC
     LIMIT ${Number(limit)}`,
    { conversationId }
  );
}

export async function addMessage(input: {
  conversationId: number;
  direction: "inbound" | "outbound";
  senderType: "contact" | "bot" | "agent" | "system";
  role: "user" | "assistant" | "human" | "system";
  content: string;
  waMessageId?: string | null;
  mediaUrl?: string | null;
  quotedText?: string | null;
  raw?: unknown;
}): Promise<number> {
  const result = await execute(
    `INSERT INTO crm_messages
      (id_conversation, direction_message, sender_type, role_message, wa_message_id,
       content_message, media_url, quoted_text, raw_message)
     VALUES
      (:conversationId, :direction, :senderType, :role, :waMessageId,
       :content, :mediaUrl, :quotedText, :raw)`,
    {
      conversationId: input.conversationId,
      direction: input.direction,
      senderType: input.senderType,
      role: input.role,
      waMessageId: input.waMessageId || null,
      content: input.content,
      mediaUrl: input.mediaUrl || null,
      quotedText: input.quotedText || null,
      raw: input.raw ? JSON.stringify(input.raw) : null,
    }
  );
  await execute(
    `UPDATE crm_conversations SET last_message_at = NOW() WHERE id_conversation = :id`,
    { id: input.conversationId }
  );
  return Number(result.insertId);
}

export async function setMode(conversationId: number, mode: "AI" | "HUMAN") {
  await execute(
    `UPDATE crm_conversations
     SET mode_conversation = :mode,
         human_support = :human,
         bot_active = :bot
     WHERE id_conversation = :id`,
    {
      mode,
      human: mode === "HUMAN" ? 1 : 0,
      bot: mode === "AI" ? 1 : 0,
      id: conversationId,
    }
  );
}

export async function setBotActive(conversationId: number, value: boolean) {
  await execute(
    `UPDATE crm_conversations SET bot_active = :value WHERE id_conversation = :id`,
    { value: value ? 1 : 0, id: conversationId }
  );
}

export async function upsertLead(input: {
  waId: string;
  name?: string;
  email?: string;
  notes?: string;
  temperatura?: string;
}) {
  const tenantId = await ensureTenantId();
  await execute(
    `INSERT INTO crm_leads (id_tenant, wa_id, nombre_lead, email_lead, notas_lead, temperatura_lead)
     VALUES (:tenantId, :waId, :name, :email, :notes, :temperatura)
     ON DUPLICATE KEY UPDATE
       nombre_lead = COALESCE(VALUES(nombre_lead), nombre_lead),
       email_lead = COALESCE(VALUES(email_lead), email_lead),
       notas_lead = COALESCE(VALUES(notas_lead), notas_lead),
       temperatura_lead = COALESCE(VALUES(temperatura_lead), temperatura_lead)`,
    {
      tenantId,
      waId: input.waId,
      name: input.name || null,
      email: input.email || null,
      notes: input.notes || null,
      temperatura: input.temperatura || null,
    }
  );
}

export async function getLeadByPhone(waId: string) {
  const tenantId = await ensureTenantId();
  const rows = await query<RowDataPacket[]>(
    `SELECT * FROM crm_leads WHERE id_tenant = :tenantId AND wa_id = :waId LIMIT 1`,
    { tenantId, waId }
  );
  return rows[0] || null;
}

export async function getMemory(waId: string) {
  const tenantId = await ensureTenantId();
  const rows = await query<RowDataPacket[]>(
    `SELECT * FROM crm_lead_memory WHERE id_tenant = :tenantId AND wa_id = :waId LIMIT 1`,
    { tenantId, waId }
  );
  return rows[0] || null;
}

export async function upsertMemory(waId: string, patch: Record<string, unknown>) {
  const tenantId = await ensureTenantId();
  const existing = await getMemory(waId);
  if (!existing) {
    await execute(
      `INSERT INTO crm_lead_memory
        (id_tenant, wa_id, nombre_memory, email_memory, objetivo_memory, situacion_memory,
         temperatura_memory, resumen_memory, first_seen, last_seen)
       VALUES
        (:tenantId, :waId, :nombre, :email, :objetivo, :situacion,
         :temperatura, :resumen, NOW(), NOW())`,
      {
        tenantId,
        waId,
        nombre: patch.nombre_memory ?? patch.name ?? null,
        email: patch.email_memory ?? patch.email ?? null,
        objetivo: patch.objetivo_memory ?? patch.objetivo ?? null,
        situacion: patch.situacion_memory ?? patch.situacion ?? null,
        temperatura: patch.temperatura_memory ?? patch.temperatura ?? null,
        resumen: patch.resumen_memory ?? patch.resumen ?? null,
      }
    );
    return;
  }
  await execute(
    `UPDATE crm_lead_memory SET
       nombre_memory = COALESCE(:nombre, nombre_memory),
       email_memory = COALESCE(:email, email_memory),
       objetivo_memory = COALESCE(:objetivo, objetivo_memory),
       situacion_memory = COALESCE(:situacion, situacion_memory),
       temperatura_memory = COALESCE(:temperatura, temperatura_memory),
       resumen_memory = COALESCE(:resumen, resumen_memory),
       last_seen = NOW()
     WHERE id_tenant = :tenantId AND wa_id = :waId`,
    {
      tenantId,
      waId,
      nombre: patch.nombre_memory ?? patch.name ?? null,
      email: patch.email_memory ?? patch.email ?? null,
      objetivo: patch.objetivo_memory ?? patch.objetivo ?? null,
      situacion: patch.situacion_memory ?? patch.situacion ?? null,
      temperatura: patch.temperatura_memory ?? patch.temperatura ?? null,
      resumen: patch.resumen_memory ?? patch.resumen ?? null,
    }
  );
}

export async function getSetting(key: string): Promise<string | null> {
  const tenantId = await ensureTenantId();
  const rows = await query<RowDataPacket[]>(
    `SELECT valor_setting FROM crm_settings WHERE id_tenant = :tenantId AND llave_setting = :key LIMIT 1`,
    { tenantId, key }
  );
  return rows[0] ? String(rows[0].valor_setting) : null;
}

export async function setSetting(key: string, value: string) {
  const tenantId = await ensureTenantId();
  await execute(
    `INSERT INTO crm_settings (id_tenant, llave_setting, valor_setting)
     VALUES (:tenantId, :key, :value)
     ON DUPLICATE KEY UPDATE valor_setting = VALUES(valor_setting)`,
    { tenantId, key, value }
  );
}

export async function enqueueOutbox(input: {
  conversationId: number;
  waId: string;
  content: string;
  type?: "text" | "image";
  mediaPath?: string | null;
}) {
  const result = await execute(
    `INSERT INTO crm_outbox (id_conversation, wa_id, content_outbox, type_outbox, media_path, status_outbox)
     VALUES (:conversationId, :waId, :content, :type, :mediaPath, 'pending')`,
    {
      conversationId: input.conversationId,
      waId: input.waId,
      content: input.content,
      type: input.type || "text",
      mediaPath: input.mediaPath || null,
    }
  );
  return Number(result.insertId);
}

export async function listPendingOutbox(limit = 20) {
  return query<RowDataPacket[]>(
    `SELECT * FROM crm_outbox WHERE status_outbox = 'pending' ORDER BY id_outbox ASC LIMIT ${Number(limit)}`
  );
}

export async function markOutbox(id: number, status: "sent" | "failed", error?: string) {
  await execute(
    `UPDATE crm_outbox
     SET status_outbox = :status,
         error_outbox = :error,
         fecha_enviado = CASE WHEN :status = 'sent' THEN NOW() ELSE fecha_enviado END
     WHERE id_outbox = :id`,
    { id, status, error: error || null }
  );
}

/** Conversaciones con último mensaje del usuario y sin respuesta en [minSec, maxSec]. */
export async function getUnansweredConversations(minSec: number, maxSec: number) {
  const tenantId = await ensureTenantId();
  return query<RowDataPacket[]>(
    `SELECT c.id_conversation, ct.wa_id AS phone, ct.nombre_contact AS name,
            lm.role_message AS last_role, lm.fecha_creacion AS last_at
     FROM crm_conversations c
     JOIN crm_contacts ct ON ct.id_contact = c.id_contact
     JOIN (
       SELECT m1.id_conversation, m1.role_message, m1.fecha_creacion
       FROM crm_messages m1
       INNER JOIN (
         SELECT id_conversation, MAX(id_message) AS max_id
         FROM crm_messages GROUP BY id_conversation
       ) t ON t.max_id = m1.id_message
     ) lm ON lm.id_conversation = c.id_conversation
     WHERE c.id_tenant = :tenantId
       AND c.mode_conversation = 'AI'
       AND c.bot_active = 1
       AND lm.role_message = 'user'
       AND lm.fecha_creacion <= DATE_SUB(NOW(), INTERVAL :minSec SECOND)
       AND lm.fecha_creacion >= DATE_SUB(NOW(), INTERVAL :maxSec SECOND)
     ORDER BY lm.fecha_creacion ASC`,
    { tenantId, minSec, maxSec }
  );
}

export async function ensureInboundConversation(waId: string, name = "") {
  const tenantId = await ensureTenantId();
  const contactId = await getOrCreateContact(tenantId, waId, name);
  const conversationId = await getOrCreateConversation(tenantId, contactId);
  return { tenantId, contactId, conversationId };
}
