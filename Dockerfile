FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Seoul \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PORT=8080

WORKDIR /app

# Playwright Chromium 구동에 필요한 시스템 패키지 설치 (libatomic1 필수)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    tzdata \
    libatomic1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium

COPY . .

# Windows gcloud 업로드 시 한글 파일명(CP949)을 UTF-8로 자동 보정
RUN python -c "import os, pathlib; p = pathlib.Path('snapshots'); \
[f.rename(p / os.fsencode(f.name).decode('cp949')) for f in list(p.iterdir()) if p.exists() and not f.name.isascii() and any(0xDC80 <= ord(c) <= 0xDCFF for c in f.name)]"

EXPOSE 8080

CMD ["sh", "-c", "streamlit run app.py --server.port=${PORT:-8080} --server.address=0.0.0.0 --server.headless=true --server.enableCORS=false --server.enableXsrfProtection=false"]
