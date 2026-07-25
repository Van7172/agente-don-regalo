FROM python:3.12-slim AS base

WORKDIR /app

# ffmpeg: convierte notas de voz webm/opus a formatos aceptados por WhatsApp.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


FROM base AS quality-gate

COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY . .

# Fail-closed: una regresión impide crear el marcador que exige runtime.
RUN python scripts/quality_gate.py --report /tmp/quality-gate.json \
    && printf "quality-gate=passed\n" > /tmp/quality-gate.passed


FROM base AS runtime

# Esta dependencia obliga a Docker a construir y aprobar quality-gate incluso
# cuando se invoca simplemente `docker build .`.
COPY --from=quality-gate /tmp/quality-gate.passed /app/.quality-gate.passed
COPY . .

EXPOSE 80

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-80}"]
