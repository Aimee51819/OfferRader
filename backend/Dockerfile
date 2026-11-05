FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .
COPY tests/fixtures ./tests/fixtures

ENV DATA_DIR=/data
VOLUME ["/data"]

EXPOSE 8000
# 0.0.0.0 inside the container only; docker-compose publishes 127.0.0.1:8080 on the host.
ENV BIND_HOST=0.0.0.0
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
