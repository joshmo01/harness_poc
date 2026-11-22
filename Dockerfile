# One image for stubs, gateway and BFF (the command picks the service).
FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY poc ./poc
COPY policies ./policies
COPY registry.yaml ./
ENV POC_DATA_DIR=/data PYTHONUNBUFFERED=1
VOLUME /data
