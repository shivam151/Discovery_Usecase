# Use official Python image as base
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Set work directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY requirements-linx.txt .
RUN pip install --upgrade pip && pip install -r requirements-linx.txt

# Copy application code
COPY . .

# Expose port (change if your FastAPI runs on another port)
EXPOSE 8000

# Start the FastAP
CMD ["uvicorn", "project_api:app", "--host", "0.0.0.0", "--port", "8000"]