FROM python:3.12-slim
WORKDIR /app
# libgomp1 is needed by LightGBM
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY ui ./ui
COPY data/docs ./data/docs
