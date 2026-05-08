FROM python:3.11-slim-bookworm

WORKDIR /app

# Copy project files
COPY pyproject.toml /app
COPY . /app

# Install dependencies
RUN apt-get update && apt-get install -y \
    libxml2-dev \
    libxslt-dev \
    python3-dev \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir -e .

# Pass prompt via args, e.g.:
#   docker run tagent --prompt "Hack the website at http://target:8080"
#   docker run -v /path/to/prompts:/prompts tagent --prompt-file /prompts/task.txt
ENTRYPOINT ["python", "-m", "tagent.main"]
CMD ["--help"]
