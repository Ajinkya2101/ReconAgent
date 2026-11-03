FROM mirror.gcr.io/library/python:3.12-slim

WORKDIR /app

# Copy requirements first so this layer caches until dependencies change.
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY recon_agent/ ./recon_agent/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Run as a non-root user. Own /app so output/ (reports, feedback.json) is writable.
# /var/lib/recon-sim is the mount point of the event-log volume (RECON_SOURCE=events);
# a new named volume takes this ownership, so the non-root user can write the log.
RUN useradd --create-home --uid 10001 recon && mkdir -p /var/lib/recon-sim && \
    chown -R recon:recon /app /var/lib/recon-sim
USER recon

EXPOSE 8000

# Liveness: the sign-in page renders without a session or any engine work.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/signin', timeout=4).status == 200 else 1)"

# The source extracts (data/*.xlsx) are not baked into the image; start_container.bat
# bind-mounts the project folder at /app so the engine reads them from /app/data.
CMD ["python", "-m", "recon_agent", "serve", "--host", "0.0.0.0", "--port", "8000"]
