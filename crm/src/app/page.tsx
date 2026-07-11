"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

type Conversation = {
  id: number;
  status: string;
  mode: string;
  bot_active: boolean;
  human_support: boolean;
  contact: { wa_id: string; name: string };
  last_message: string;
};

type Message = {
  id: number;
  direction: string;
  sender_type: string;
  role: string;
  content: string;
  created_at: string;
};

type AuthUser = {
  id: number;
  login: string;
  name: string;
  email: string;
  role: string;
};

export default function InboxPage() {
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [selected, setSelected] = useState<Conversation | null>(null);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    void (async () => {
      const res = await fetch("/api/auth/me");
      if (!res.ok) {
        router.replace("/login");
        return;
      }
      const json = await res.json();
      setUser(json.user);
    })();
  }, [router]);

  const loadList = useCallback(async () => {
    try {
      const res = await fetch("/api/conversations");
      if (res.status === 401) {
        router.replace("/login");
        return;
      }
      const json = await res.json();
      if (!res.ok) throw new Error(json.error || "Error cargando inbox");
      setConversations(json.data || []);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [router]);

  const loadThread = useCallback(async (id: number) => {
    setLoading(true);
    try {
      const res = await fetch(`/api/conversations/${id}`);
      const json = await res.json();
      if (!res.ok) throw new Error(json.error || "Error cargando chat");
      setSelected(json.conversation);
      setMessages(json.messages || []);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!user) return;
    loadList();
    const t = setInterval(loadList, 8000);
    return () => clearInterval(t);
  }, [loadList, user]);

  useEffect(() => {
    if (selectedId == null) return;
    loadThread(selectedId);
    const t = setInterval(() => loadThread(selectedId), 5000);
    return () => clearInterval(t);
  }, [selectedId, loadThread]);

  async function logout() {
    await fetch("/api/auth/logout", { method: "POST" });
    router.replace("/login");
    router.refresh();
  }

  async function setMode(mode: "AI" | "HUMAN") {
    if (selectedId == null) return;
    const res = await fetch(`/api/conversations/${selectedId}/mode`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    const json = await res.json();
    if (!res.ok) {
      setError(json.error || "No se pudo cambiar modo");
      return;
    }
    await loadList();
    await loadThread(selectedId);
  }

  async function send() {
    if (selectedId == null || !draft.trim()) return;
    const content = draft.trim();
    setDraft("");
    const res = await fetch("/api/outbox", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_id: selectedId, content }),
    });
    const json = await res.json();
    if (!res.ok) {
      setError(json.error || "No se pudo encolar mensaje");
      setDraft(content);
      return;
    }
    await loadThread(selectedId);
    await loadList();
  }

  if (!user) {
    return <div className="empty">Cargando sesión…</div>;
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <h1>Don Regalo</h1>
          <p>Inbox WhatsApp · CRM</p>
        </div>
        <div className="userbar">
          <span>
            <strong>{user.name}</strong>
            <br />
            {user.role}
          </span>
          <button type="button" onClick={() => void logout()}>
            Salir
          </button>
        </div>
        <div className="list">
          {conversations.map((c) => (
            <button
              key={c.id}
              className={`item ${selectedId === c.id ? "active" : ""}`}
              onClick={() => setSelectedId(c.id)}
            >
              <div className="name">
                <span>{c.contact.name || c.contact.wa_id}</span>
                <span className={`badge ${c.mode === "HUMAN" ? "human" : "ai"}`}>
                  {c.mode}
                </span>
              </div>
              <div className="preview">{c.last_message || "Sin mensajes"}</div>
            </button>
          ))}
          {!conversations.length && (
            <div className="empty">Aún no hay conversaciones CRM.</div>
          )}
        </div>
      </aside>

      <main className="main">
        {!selected ? (
          <div className="empty">Selecciona una conversación</div>
        ) : (
          <>
            <div className="toolbar">
              <div>
                <h2>{selected.contact.name || selected.contact.wa_id}</h2>
                <div className="meta">
                  {selected.contact.wa_id} · modo {selected.mode}
                  {loading ? " · actualizando…" : ""}
                </div>
              </div>
              <div className="actions">
                <button onClick={() => setMode("AI")}>Modo AI</button>
                <button className="primary" onClick={() => setMode("HUMAN")}>
                  Tomar (humano)
                </button>
              </div>
            </div>

            <div className="thread">
              {messages.map((m) => (
                <div
                  key={m.id}
                  className={`bubble ${m.direction === "inbound" ? "inbound" : "outbound"}`}
                >
                  <div className="who">{m.sender_type}</div>
                  {m.content}
                </div>
              ))}
            </div>

            <div className="composer">
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder="Escribe como asesor… (pasa a HUMAN y envía)"
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void send();
                  }
                }}
              />
              <button onClick={() => void send()}>Enviar</button>
            </div>
          </>
        )}
        {error ? (
          <div className="empty" style={{ color: "var(--human)" }}>
            {error}
          </div>
        ) : null}
      </main>
    </div>
  );
}
