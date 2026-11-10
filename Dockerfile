FROM python:3.11-slim

# Working directory
WORKDIR /app

# Environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PORT=7860

# Install minimal system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy full application codebase
COPY . .

# Expose Hugging Face Spaces default port (7860) and Render/local port (8000)
EXPOSE 10000 8000 7860

# Container Healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:${PORT:-10000}/health || exit 1

# Launch Uvicorn using the environment-provided PORT
CMD ["sh", "-c", "uvicorn target_app.app:app --host 0.0.0.0 --port ${PORT:-10000}"]

