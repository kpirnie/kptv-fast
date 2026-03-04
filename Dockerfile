# Multi-stage Dockerfile for Unified Streaming Aggregator

###########################################
# Build Stage
###########################################
FROM docker.io/python:3.12-alpine3.20 AS builder

# Set build environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Install build dependencies
RUN apk add --no-cache \
    gcc \
    musl-dev \
    libffi-dev \
    openssl-dev \
    cargo \
    rust

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy requirements and install Python packages
COPY requirements.txt /tmp/
RUN pip install --no-cache-dir \
    --disable-pip-version-check \
    --no-compile \
    --upgrade pip setuptools wheel && \
    pip install --no-cache-dir \
    --disable-pip-version-check \
    --no-compile \
    -r /tmp/requirements.txt

# Clean up build artifacts
RUN find /opt/venv -name "*.pyc" -delete && \
    find /opt/venv -name "__pycache__" -delete

###########################################
# Runtime Stage
###########################################
FROM docker.io/python:3.12-alpine3.20 AS runtime

# Set runtime environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PATH="/opt/venv/bin:$PATH"

# Install only runtime dependencies and fix DNS issues
RUN apk add --no-cache \
    libffi \
    openssl \
    ca-certificates \
    tzdata \
    curl \
    bind-tools && \
    # Create app user for security
    adduser -D -s /bin/sh -u 1000 appuser

# Copy virtual environment from builder stage
COPY --from=builder /opt/venv /opt/venv

# Set working directory
WORKDIR /app

# Copy application code with proper ownership
COPY --chown=appuser:appuser app.py ./
COPY --chown=appuser:appuser providers/ ./providers/
COPY --chown=appuser:appuser utils/ ./utils/
COPY --chown=appuser:appuser core/   ./core/
COPY --chown=appuser:appuser routes/ ./routes/

# Switch to non-root user
USER appuser

# Run the application
CMD ["python3", "app.py"]