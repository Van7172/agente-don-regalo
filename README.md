# Agente Don Regalo

Agente de WhatsApp basado en FastAPI que recibe webhooks de Chatwoot, procesa mensajes de texto, audio e imágenes con OpenAI, y responde automáticamente en la conversación.

## Requisitos

- Python 3.11+
- Instancia de Chatwoot con webhook configurado
- API key de OpenAI

## Instalación

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
cp .env.example .env     # Edita .env con tus credenciales
```

## Uso

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Configura en Chatwoot un webhook apuntando a `https://tu-dominio/webhook` con el evento `message_created`.

## Estructura del proyecto

```
app/
├── main.py              # Crea la app FastAPI y registra routers
├── config.py            # Configuración centralizada (Settings)
├── api/                 # Capa HTTP (handlers delgados)
│   ├── webhook.py       #   POST /webhook (Chatwoot)
│   └── evolution.py     #   POST /evolution-webhook
├── services/            # Lógica de negocio
│   ├── buffer.py        #   Debounce y orquestación del flush
│   ├── agent.py         #   Loop LLM con function calling
│   ├── content.py       #   Audio / imagen / PDF
│   ├── memory.py        #   Memoria corto y largo plazo
│   ├── messenger.py     #   Envío de mensajes e imágenes
│   └── knowledge.py     #   Captura de conocimiento del equipo
├── tools/               # Function calling del agente
│   ├── definitions.py   #   Esquemas OpenAI
│   ├── catalog.py       #   Endpoints HTTP del catálogo (+ caché TTL)
│   ├── search.py        #   Búsqueda semántica (Qdrant)
│   └── executor.py      #   Dispatcher de herramientas
└── prompts/
    └── system.py        # System prompt del agente

scripts de mantenimiento (raíz):
  sync_qdrant.py         # Sincroniza el catálogo a Qdrant (nightly)
  sync_conocimiento.py   # Backfill del conocimiento del equipo
```

## Variables de entorno

| Variable | Descripción |
|---|---|
| `CHATWOOT_URL` | URL base de Chatwoot |
| `CHATWOOT_API_TOKEN` | Token de API de Chatwoot |
| `CHATWOOT_ACCOUNT_ID` | ID de la cuenta |
| `OPENAI_API_KEY` | API key de OpenAI |
| `OPENAI_MODEL` | Modelo a utilizar (ej. `gpt-4o-mini`) |
