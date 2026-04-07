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

FROM python:3.12-slim

# 明示的にHOMEを定義
ENV HOME=/root

# 非バッファリング（ログをすぐ出力）と.pycファイル生成抑制
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

# Install Rust toolchain (required for some dependencies)
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

# uvとmaturinインストール
RUN pip install uv maturin

# Copy requirements and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code to build Rust extension
COPY . .

# Build Rust extension
WORKDIR /app/lib/rust_lib
RUN maturin develop --release
# Note: 'maturin develop' installs into the current venv

# --- Stage 2: Runtime ---
FROM python:3.11-slim AS runtime

# Runtime environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# 依存関係を先にコピーしてインストール（キャッシュを活用）
COPY requirements.txt /app/requirements.txt
RUN uv pip install --system --upgrade pip setuptools wheel \
    && uv pip install --system --no-cache-dir -r /app/requirements.txt

# Copy virtual environment from builder
COPY --from=builder --chown=${USER}:${USER} /app/.venv /app/.venv

# Rustバインディングをリリースビルド
RUN cd lib/rust_lib && maturin build --release && uv pip install --system target/wheels/*.whl --reinstall

# Create tmp directory with correct permissions
RUN mkdir -p /app/tmp && chown -R ${USER}:${USER} /app/tmp

# Switch to non-root user
USER ${USER}

# Default command
CMD ["python", "bot.py"]
