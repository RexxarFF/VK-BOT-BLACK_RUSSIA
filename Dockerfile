FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV DATA_DIR=/app/data
RUN mkdir -p /app/data /app/logs && chmod -R 777 /app/data /app/logs

CMD ["python", "main.py"]
