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
uvicorn main:app --host 0.0.0.0 --port 8000
```

Configura en Chatwoot un webhook apuntando a `https://tu-dominio/webhook` con el evento `message_created`.

## Variables de entorno

| Variable | Descripción |
|---|---|
| `CHATWOOT_URL` | URL base de Chatwoot |
| `CHATWOOT_API_TOKEN` | Token de API de Chatwoot |
| `CHATWOOT_ACCOUNT_ID` | ID de la cuenta |
| `OPENAI_API_KEY` | API key de OpenAI |
| `OPENAI_MODEL` | Modelo a utilizar (ej. `gpt-4o-mini`) |
