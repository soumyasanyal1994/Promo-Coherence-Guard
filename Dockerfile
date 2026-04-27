FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

# Ensure bundled sample files exist inside the image.
RUN python scripts/generate_synthetic_data.py

EXPOSE 8501

CMD ["uvicorn", "promo_guard.app:app", "--host", "0.0.0.0", "--port", "8501"]
