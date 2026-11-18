FROM python:3.12-slim

# Baixo consumo de memória: sem .pyc, menos arenas do malloc, sem compilador no build
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MALLOC_ARENA_MAX=2 \
    PORT=5000 \
    DATA_DIR=/data

WORKDIR /app

# psycopg2-binary e pillow já vêm em wheel: não precisa de gcc/libpq-dev
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia app.py, templates/, etc.
COPY . .

# Pasta persistente: no Dokploy monte um volume em /data (imagens em /data/uploads)
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 5000

# 1 worker + 4 threads = ~metade da RAM de 2 workers; recicla o worker de tempos em tempos
CMD ["sh", "-c", "exec gunicorn app:app --bind 0.0.0.0:${PORT} --workers 1 --threads 4 --timeout 60 --max-requests 1000 --max-requests-jitter 100 --worker-tmp-dir /dev/shm"]
