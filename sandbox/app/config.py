"""
Configuración del sandbox (WhatsApp Cloud API + CRM). Sin Chatwoot/Evolution.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


class Settings:
    def __init__(self) -> None:
        self.whatsapp_token: str = os.getenv("WHATSAPP_TOKEN", "")
        self.whatsapp_phone_number_id: str = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
        self.whatsapp_verify_token: str = os.getenv("WHATSAPP_VERIFY_TOKEN", "change-me")
        self.whatsapp_app_secret: str = os.getenv("WHATSAPP_APP_SECRET", "")
        self.whatsapp_api_version: str = os.getenv("WHATSAPP_API_VERSION", "v21.0")
        self.whatsapp_graph_url: str = (
            f"https://graph.facebook.com/{self.whatsapp_api_version}"
        )

        self.database_url: str = os.getenv(
            "DATABASE_URL", "sqlite+aiosqlite:///./sandbox.db"
        )
        self.default_tenant_slug: str = os.getenv("DEFAULT_TENANT_SLUG", "don-regalo")
        self.default_tenant_name: str = os.getenv("DEFAULT_TENANT_NAME", "Don Regalo")

        self.openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
        self.openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

        self.bot_active_label: str = os.getenv("BOT_ACTIVE_LABEL", "agente_on")
        self.human_support_label: str = os.getenv("HUMAN_SUPPORT_LABEL", "soporte_humano")
        self.alert_webhook_url: str = os.getenv("ALERT_WEBHOOK_URL", "")

        self.pdf_max_chars: int = int(os.getenv("PDF_MAX_CHARS", "30000"))
        self.buffer_seconds: float = float(os.getenv("BUFFER_SECONDS", "2.5"))
        self.typing_seconds_per_char: float = float(
            os.getenv("TYPING_SECONDS_PER_CHAR", "0.01")
        )
        self.typing_min_delay: float = float(os.getenv("TYPING_MIN_DELAY", "0.2"))
        self.typing_max_delay: float = float(os.getenv("TYPING_MAX_DELAY", "1.2"))

        self.memory_window_hours: float = float(os.getenv("MEMORY_WINDOW_HOURS", "12"))
        self.memory_max_messages: int = int(os.getenv("MEMORY_MAX_MESSAGES", "15"))
        self.max_tool_rounds: int = int(os.getenv("MAX_TOOL_ROUNDS", "4"))

        self.qdrant_url: str = os.getenv("QDRANT_URL", "").rstrip("/")
        self.qdrant_api_key: str = os.getenv("QDRANT_API_KEY", "")
        self.qdrant_collection: str = os.getenv("QDRANT_COLLECTION", "productos")
        self.embed_model: str = os.getenv("EMBED_MODEL", "text-embedding-3-small")
        self.embed_dim: int = int(os.getenv("EMBED_DIM", "1536"))
        self.semantic_limit: int = int(os.getenv("SEMANTIC_LIMIT", "6"))
        self.kb_collection: str = os.getenv("KB_COLLECTION", "respuestas_equipo")
        self.kb_limit: int = int(os.getenv("KB_LIMIT", "3"))
        self.kb_min_score: float = float(os.getenv("KB_MIN_SCORE", "0.5"))
        self.cache_ttl_seconds: float = float(os.getenv("CACHE_TTL_SECONDS", "3600"))
        self.donregalo_api_base: str = os.getenv(
            "DONREGALO_API_BASE", "https://donregalo.pe/clienteApiApp/api"
        )


settings = Settings()
