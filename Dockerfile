FROM python:3.11-slim

WORKDIR /app

# System dependencies needed by Chromium
RUN apt-get update && apt-get install -y \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    libatspi2.0-0 libx11-6 libx11-xcb1 libxcb1 libxext6 libxi6 \
    libxcursor1 libxrender1 libxtst6 libgtk-3-0 ca-certificates \
    fonts-liberation wget \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 🔥 THIS IS THE KEY LINE (forces correct browser install)
RUN python -m playwright install chromium

COPY . .
CMD ["python", "-u", "shein_watch.py"]
