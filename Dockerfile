FROM python:3.11-slim

WORKDIR /app

# Install curl for healthcheck
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app app/
COPY templates templates/

# Run the application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7000"]
