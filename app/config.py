"""
Configuración del agente (WhatsApp Cloud API + CRM). Sin Chatwoot/Evolution.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

_SANDBOX_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _SANDBOX_ROOT.parent
# Primero raíz (OpenAI/Qdrant legacy), luego sandbox/.env pisa lo local (CRM/Meta)
load_dotenv(_REPO_ROOT / ".env")
load_dotenv(_SANDBOX_ROOT / ".env", override=True)


class Settings:
    def __init__(self) -> None:
        self.whatsapp_token: str = os.getenv("WHATSAPP_TOKEN", "")
        self.whatsapp_phone_number_id: str = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
        self.whatsapp_verify_token: str = os.getenv("WHATSAPP_VERIFY_TOKEN", "change-me")
        self.whatsapp_app_secret: str = os.getenv("WHATSAPP_APP_SECRET", "")
        self.whatsapp_api_version: str = os.getenv("WHATSAPP_API_VERSION", "v22.0")
        self.whatsapp_graph_url: str = (
            f"https://graph.facebook.com/{self.whatsapp_api_version}"
        )
        # 1 = no llama a Graph API; simula envíos (útil para E2E local)
        self.whatsapp_dry_run: bool = os.getenv("WHATSAPP_DRY_RUN", "0") == "1"

        self.database_url: str = os.getenv(
            "DATABASE_URL", "sqlite+aiosqlite:///./sandbox.db"
        )
        # Opción C: CRM externo (PHP + MySQL en hosting). local = SQLite embebido.
        # local = SQLite sandbox (tests); external = CRM PHP (hosting) vía HTTP
        self.crm_mode: str = os.getenv("CRM_MODE", "local").strip().lower()
        self.crm_base_url: str = os.getenv("CRM_BASE_URL", "http://127.0.0.1:3100").rstrip("/")
        self.crm_internal_token: str = os.getenv("CRM_INTERNAL_TOKEN", "dev-crm-token-change-me").strip()
        self.agent_internal_token: str = os.getenv("AGENT_INTERNAL_TOKEN", "dev-agent-token-change-me")
        self.alert_whatsapp: str = os.getenv("ALERT_WHATSAPP", "").replace("+", "").strip()
        self.watchdog_enabled: bool = os.getenv("WATCHDOG_ENABLED", "1") == "1"
        self.watchdog_tick_seconds: float = float(os.getenv("WATCHDOG_TICK_SECONDS", "300"))
        self.default_tenant_slug: str = os.getenv("DEFAULT_TENANT_SLUG", "don-regalo")
        self.default_tenant_name: str = os.getenv("DEFAULT_TENANT_NAME", "Don Regalo")

        self.openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
        self.openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        # Clasificador de intención del router: solo se usa cuando las reglas no
        # saben, así que puede ser el modelo más barato disponible.
        self.router_model: str = os.getenv("ROUTER_MODEL", "gpt-4o-mini")

        self.bot_active_label: str = os.getenv("BOT_ACTIVE_LABEL", "agente_on")
        self.human_support_label: str = os.getenv("HUMAN_SUPPORT_LABEL", "soporte_humano")
        self.alert_webhook_url: str = os.getenv("ALERT_WEBHOOK_URL", "")

        self.pdf_max_chars: int = int(os.getenv("PDF_MAX_CHARS", "30000"))
        self.buffer_seconds: float = float(os.getenv("BUFFER_SECONDS", "2.5"))
        # El webhook entrega cada mensaje a una cola acotada y responde de
        # inmediato. Un worker conserva el orden por defecto.
        self.inbound_queue_maxsize: int = max(
            1, int(os.getenv("INBOUND_QUEUE_MAXSIZE", "500"))
        )
        self.inbound_queue_workers: int = max(
            1, int(os.getenv("INBOUND_QUEUE_WORKERS", "1"))
        )
        self.inbound_queue_shutdown_seconds: float = max(
            0.1, float(os.getenv("INBOUND_QUEUE_SHUTDOWN_SECONDS", "20"))
        )
        self.inbound_queue_backend: str = (
            os.getenv("INBOUND_QUEUE_BACKEND", "local").strip().lower()
        )
        self.redis_url: str = os.getenv("REDIS_URL", "").strip()
        self.redis_stream_key: str = os.getenv(
            "REDIS_STREAM_KEY", "donregalo:inbound"
        ).strip()
        self.redis_consumer_group: str = os.getenv(
            "REDIS_CONSUMER_GROUP", "donregalo-agents"
        ).strip()
        self.redis_dlq_stream: str = os.getenv(
            "REDIS_DLQ_STREAM", "donregalo:inbound:dlq"
        ).strip()
        self.redis_block_ms: int = max(
            100, int(os.getenv("REDIS_BLOCK_MS", "2000"))
        )
        self.redis_claim_idle_ms: int = max(
            1000, int(os.getenv("REDIS_CLAIM_IDLE_MS", "60000"))
        )
        self.redis_reclaim_seconds: float = max(
            0.1, float(os.getenv("REDIS_RECLAIM_SECONDS", "15"))
        )
        self.redis_dedupe_ttl_seconds: int = max(
            60, int(os.getenv("REDIS_DEDUPE_TTL_SECONDS", "86400"))
        )
        self.redis_lock_ttl_seconds: float = max(
            1.0, float(os.getenv("REDIS_LOCK_TTL_SECONDS", "120"))
        )
        self.redis_lock_wait_seconds: float = max(
            0.1, float(os.getenv("REDIS_LOCK_WAIT_SECONDS", "10"))
        )
        self.inbound_max_retries: int = max(
            1, int(os.getenv("INBOUND_MAX_RETRIES", "3"))
        )
        self.inbound_retry_base_seconds: float = max(
            0.0, float(os.getenv("INBOUND_RETRY_BASE_SECONDS", "1"))
        )
        self.typing_seconds_per_char: float = float(
            os.getenv("TYPING_SECONDS_PER_CHAR", "0.01")
        )
        self.typing_min_delay: float = float(os.getenv("TYPING_MIN_DELAY", "0.2"))
        self.typing_max_delay: float = float(os.getenv("TYPING_MAX_DELAY", "1.2"))

        self.memory_window_hours: float = float(os.getenv("MEMORY_WINDOW_HOURS", "12"))
        self.memory_max_messages: int = int(os.getenv("MEMORY_MAX_MESSAGES", "15"))
        self.max_tool_rounds: int = int(os.getenv("MAX_TOOL_ROUNDS", "4"))
        # Harness: auto-retorno HUMAN→AI (segundos de inactividad del asesor).
        self.harness_releaser_sec: float = float(os.getenv("HARNESS_RELEASER_SEC", "1200"))
        self.harness_payment_releaser_sec: float = float(
            os.getenv("HARNESS_PAYMENT_RELEASER_SEC", "7200")
        )

        self.qdrant_url: str = os.getenv("QDRANT_URL", "").rstrip("/")
        self.qdrant_api_key: str = os.getenv("QDRANT_API_KEY", "")
        self.qdrant_collection: str = os.getenv("QDRANT_COLLECTION", "productos")
        self.embed_model: str = os.getenv("EMBED_MODEL", "text-embedding-3-small")
        self.embed_dim: int = int(os.getenv("EMBED_DIM", "1536"))
        self.semantic_limit: int = int(os.getenv("SEMANTIC_LIMIT", "6"))
        self.embedding_worker_enabled: bool = (
            os.getenv("EMBEDDING_WORKER_ENABLED", "0") == "1"
        )
        self.embedding_worker_tick_seconds: float = max(
            2.0, float(os.getenv("EMBEDDING_WORKER_TICK_SECONDS", "30"))
        )
        self.embedding_worker_batch: int = max(
            1, min(50, int(os.getenv("EMBEDDING_WORKER_BATCH", "10")))
        )
        self.kb_collection: str = os.getenv("KB_COLLECTION", "respuestas_equipo")
        self.kb_limit: int = int(os.getenv("KB_LIMIT", "3"))
        self.kb_min_score: float = float(os.getenv("KB_MIN_SCORE", "0.5"))
        self.cache_ttl_seconds: float = float(os.getenv("CACHE_TTL_SECONDS", "3600"))
        # Protección común de dependencias externas. Tras varios fallos seguidos
        # se evita insistir durante una pausa y luego se permite una sola prueba.
        self.circuit_breaker_failure_threshold: int = max(
            1, int(os.getenv("CIRCUIT_BREAKER_FAILURE_THRESHOLD", "5"))
        )
        self.circuit_breaker_recovery_seconds: float = max(
            0.0, float(os.getenv("CIRCUIT_BREAKER_RECOVERY_SECONDS", "30"))
        )
        self.donregalo_api_base: str = os.getenv(
            "DONREGALO_API_BASE", "https://donregalo.pe/clienteApiApp/api"
        )
        # MCP del catálogo de Don Regalo. Camino alterno para las lecturas de
        # catálogo/detalle/pagos: mismas respuestas canónicas, pero con imagen_url
        # viva del servidor. OPT-IN: con `DONREGALO_USE_MCP=0` (por defecto) el bot
        # sigue con HTTP directo y nada cambia. Si el MCP falla, se degrada a HTTP.
        self.donregalo_mcp_url: str = os.getenv(
            "DONREGALO_MCP_URL", "https://www.donregalo.pe/clienteApiApp/mcp/"
        )
        # El token NUNCA se hardcodea: sale del entorno. Sin token, no se usa MCP.
        self.donregalo_mcp_token: str = os.getenv("DONREGALO_MCP_TOKEN", "")
        self.donregalo_use_mcp: bool = (
            os.getenv("DONREGALO_USE_MCP", "0") == "1"
            and bool(os.getenv("DONREGALO_MCP_TOKEN", ""))
        )
        # Al cerrar una venta, crear el pedido temporal en el panel de donregalo
        # (`POST /pedidos/temporales`). Se puede apagar sin tocar código.
        self.pedido_temporal_enabled: bool = (
            os.getenv("PEDIDO_TEMPORAL_ENABLED", "1") == "1"
        )


settings = Settings()
