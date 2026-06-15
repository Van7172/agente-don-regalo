import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    def __init__(self) -> None:
        self.chatwoot_url: str         = os.getenv("CHATWOOT_URL", "").rstrip("/")
        self.chatwoot_api_token: str   = os.getenv("CHATWOOT_API_TOKEN", "")
        self.chatwoot_account_id: str  = os.getenv("CHATWOOT_ACCOUNT_ID", "")

        self.openai_api_key: str       = os.getenv("OPENAI_API_KEY", "")
        self.openai_model: str         = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

        self.bot_active_label: str     = os.getenv("BOT_ACTIVE_LABEL", "agente_on")

        self.evolution_api_url: str    = os.getenv("EVOLUTION_API_URL", "").rstrip("/")
        self.evolution_api_key: str    = os.getenv("EVOLUTION_API_KEY", "")
        self.evolution_instance: str   = os.getenv("EVOLUTION_INSTANCE", "")

        self.pdf_max_chars: int        = int(os.getenv("PDF_MAX_CHARS", "30000"))
        self.buffer_seconds: float     = float(os.getenv("BUFFER_SECONDS", "6"))

        self.typing_seconds_per_char: float = float(os.getenv("TYPING_SECONDS_PER_CHAR", "0.03"))
        self.typing_min_delay: float        = float(os.getenv("TYPING_MIN_DELAY", "0.8"))
        self.typing_max_delay: float        = float(os.getenv("TYPING_MAX_DELAY", "4.0"))

        self.memory_window_hours: float = float(os.getenv("MEMORY_WINDOW_HOURS", "24"))
        self.memory_max_messages: int   = int(os.getenv("MEMORY_MAX_MESSAGES", "30"))
        self.max_tool_rounds: int       = int(os.getenv("MAX_TOOL_ROUNDS", "6"))

        self.qdrant_url: str           = os.getenv("QDRANT_URL", "").rstrip("/")
        self.qdrant_api_key: str       = os.getenv("QDRANT_API_KEY", "")
        self.qdrant_collection: str    = os.getenv("QDRANT_COLLECTION", "productos")

        self.embed_model: str          = os.getenv("EMBED_MODEL", "text-embedding-3-small")
        self.embed_dim: int            = int(os.getenv("EMBED_DIM", "1536"))
        self.semantic_limit: int       = int(os.getenv("SEMANTIC_LIMIT", "6"))

        self.kb_collection: str        = os.getenv("KB_COLLECTION", "respuestas_equipo")
        self.kb_limit: int             = int(os.getenv("KB_LIMIT", "3"))
        self.kb_min_score: float       = float(os.getenv("KB_MIN_SCORE", "0.5"))

        self.cache_ttl_seconds: float  = float(os.getenv("CACHE_TTL_SECONDS", "3600"))
        self.donregalo_api_base: str   = os.getenv(
            "DONREGALO_API_BASE", "https://donregalo.pe/clienteApiApp/api"
        )


settings = Settings()
