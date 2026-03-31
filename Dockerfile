FROM python:3.12-slim

WORKDIR /app

# install deps first so Docker can cache this layer
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# copy source
COPY app/ ./app/
COPY frontend/ ./frontend/

# SQLite DB will live in /tmp — fine for a dev tool, swap for a volume in prod
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
