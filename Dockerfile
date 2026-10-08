FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY pyproject.toml README.md ./
COPY untangle ./untangle
RUN pip install --no-cache-dir .
CMD ["sh", "-c", "uvicorn untangle.api:app --host 0.0.0.0 --port ${PORT:-8080}"]
