FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    python -m playwright install firefox && \
    python -m playwright install-deps firefox

COPY . .

# Persist SQLite data
VOLUME ["/app/data"]

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
