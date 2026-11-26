FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=8080
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# staged by deploy.sh: app/, site_lib/ (copies of online-store/app/{catalog,search,ai}.py), static/,
# product_images/ (online-store/static/img/products), data/ (store-navi/data + products_retail.jsonl + autocomplete)
COPY app ./app
COPY site_lib ./site_lib
COPY static ./static
COPY product_images ./product_images
COPY data ./data
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'
