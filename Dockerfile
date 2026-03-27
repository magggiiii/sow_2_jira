FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for PyMuPDF and other C-based libs
RUN apt-get update && apt-get install -y \
    build-essential \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Ensure data directory exists inside container
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uvicorn", "ui.server:app", "--host", "0.0.0.0", "--port", "8000"]
