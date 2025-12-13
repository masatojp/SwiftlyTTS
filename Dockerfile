# SwiftlyTTS Dockerfile (Optimized Multi-stage Build)

# --- Stage 1: Builder ---
FROM python:3.11-slim AS builder

# Prevent writing .pyc files
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

# Create a virtual environment for dependencies
RUN python -m venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Install maturin separately
RUN pip install maturin

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

# Create non-root user
ARG USER=bot
ARG UID=1000
ARG GID=1000

RUN groupadd -g ${GID} ${USER} \
    && useradd -u ${UID} -g ${GID} -m ${USER}

# Install runtime dependencies (FFmpeg is required for voice)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder --chown=${USER}:${USER} /app/.venv /app/.venv

# Copy application code
COPY --chown=${USER}:${USER} . .

# Switch to non-root user
USER ${USER}

# Default command
CMD ["python", "bot.py"]
