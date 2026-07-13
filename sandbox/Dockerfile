FROM python:3.12-slim

WORKDIR /app

# ffmpeg: el navegador del asesor graba las notas de voz en webm/opus y WhatsApp
# solo acepta ogg/opus, mp3, aac o mp4. Sin esto, el audio del asesor no sale.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 80

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-80}"]
