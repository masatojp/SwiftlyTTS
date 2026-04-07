# SwiftlyTTS Discord ボットを実行するための Dockerfile
# 使用方法:
# - ビルド: docker build -t swiftlytts-bot:latest .
# - 実行 (.env をマウントするか、環境変数を渡します):
# docker run --rm -e DISCORD_TOKEN=... -e DB_HOST=... -e DB_USER=... -e DB_PASSWORD=... swiftlytts-bot:latest
# 必須の環境変数 (少なくとも):
# - DISCORD_TOKEN: Discord ボットトークン
# - DB_HOST、DB_PORT、DB_NAME、DB_USER、DB_PASSWORD: PostgreSQL 接続
# オプション:
# - SHARD_COUNT: シャード数 (デフォルト 3)

# --- Stage 1: Builder ---
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    libffi-dev \
    libssl-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Install Rust toolchain (required for rust extension)
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

RUN pip install uv

COPY requirements.txt .

# Create virtual environment
RUN uv venv /app/.venv
ENV VIRTUAL_ENV=/app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Install python dependencies and maturin
RUN uv pip install -r requirements.txt maturin

COPY . .

# Build Rust extension
WORKDIR /app/lib/rust_lib
RUN maturin develop --release

# --- Stage 2: Runtime ---
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PATH="/app/.venv/bin:$PATH"

# Install ffmpeg (required for Discord TTS audio encoding)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Copy application code
COPY . .

# Create tmp directory for TTS processing
RUN mkdir -p /app/tmp

CMD ["python", "bot.py"]
