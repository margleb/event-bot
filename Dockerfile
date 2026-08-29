FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY main.py ./

RUN pip install --no-cache-dir .

RUN mkdir -p /app/data

CMD ["python", "main.py"]
