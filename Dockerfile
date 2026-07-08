FROM python:3.11-slim

WORKDIR /app

# Install system build dependencies if needed (slim has minimal tooling)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy package definition, readme, and code
COPY pyproject.toml README.md /app/
COPY src/ /app/src/
COPY pipelines/ /app/pipelines/
COPY main.py /app/

# Install dependencies and build-system
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

# Set entrypoint to CLI runner
ENTRYPOINT ["python", "main.py"]
