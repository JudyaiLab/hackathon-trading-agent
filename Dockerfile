FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for Kraken CLI
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Kraken CLI (hackathon requirement)
# Pre-built binary from GitHub releases — fails hard if unavailable
RUN curl -fsSL https://github.com/kraken-exchange/kraken-cli/releases/latest/download/kraken-linux-amd64 \
    -o /usr/local/bin/kraken && chmod +x /usr/local/bin/kraken \
    && kraken --version \
    || { echo "ERROR: Kraken CLI installation failed. Provide binary via volume mount: -v /path/to/kraken:/usr/local/bin/kraken"; exit 1; }

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/logs /app/validation

# Health check — verify agent can start
HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD python3 agent.py --status || exit 1

ENTRYPOINT ["python3"]
CMD ["agent.py", "--status"]
