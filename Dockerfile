FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

# Ensure bundled sample files exist inside the image.
RUN python scripts/generate_synthetic_data.py

EXPOSE 8501

CMD ["streamlit", "run", "promo_guard/app.py", "--server.address=0.0.0.0", "--server.port=8501"]
