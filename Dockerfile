FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RA_HOST=0.0.0.0 \
    RA_PORT=5080

COPY server/requirements.txt /app/server/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/server/requirements.txt

COPY server /app/server
COPY agent /app/agent

RUN mkdir -p /app/data

WORKDIR /app/server
EXPOSE 5080

CMD ["python", "app.py", "--host", "0.0.0.0", "--port", "5080", "--no-browser"]

