FROM python:3.12-slim

# Deps do sistema para Playwright/Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0t64 libatk-bridge2.0-0t64 \
    libcups2t64 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \
    libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 \
    libasound2t64 libdbus-1-3 libx11-xcb1 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instala dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Instala browser do Playwright
RUN playwright install chromium

# Copia codigo
COPY . .

# Porta configuravel pela Koyeb
EXPOSE 8000

# Koyeb injeta $PORT dinamicamente
CMD fastapi run src/main.py --port ${PORT:-8000} --host 0.0.0.0 --workers 1
