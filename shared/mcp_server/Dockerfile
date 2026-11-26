FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PORT=8080
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# server.py + data/ (bundled fallback: personas.json, purchase_history.jsonl)
COPY server.py dinner.py ./
COPY data/ ./data/
EXPOSE 8080
CMD ["python", "server.py"]
