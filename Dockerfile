FROM mirror.gcr.io/library/python:3.12-slim

# Set by the release workflow (the git tag without the v); shows in the image labels.
ARG VERSION=dev
LABEL org.opencontainers.image.title="recon-agent" \
      org.opencontainers.image.description="Inventory reconciliation worklist for MIPS, WMS and Oracle" \
      org.opencontainers.image.source="https://github.com/Ajinkya2101/ReconAgent" \
      org.opencontainers.image.version="${VERSION}"

WORKDIR /app

# Copy requirements first so this layer caches until dependencies change.
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY recon_agent/ ./recon_agent/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Run as a non-root user. It owns output/ (reports, decisions, accounts, the event log) and data/
# (mount point of the daily extracts). /var/lib/recon-sim is the event-log volume of
# start_container.bat; a new named volume takes this ownership, so the non-root user can write it.
RUN useradd --create-home --uid 10001 recon && mkdir -p /app/output /app/data /var/lib/recon-sim && \
    chown -R recon:recon /app /var/lib/recon-sim
USER recon

EXPOSE 8000

# Liveness: /healthz answers without a session or any engine work.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"

# The source extracts (data/*.xlsx) are not baked into the image: mount them at /app/data
# (docker-compose.yml), or bind-mount the project folder at /app (start_container.bat).
CMD ["python", "-m", "recon_agent", "serve", "--host", "0.0.0.0", "--port", "8000"]
