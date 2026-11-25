FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY marmo_sales ./marmo_sales
COPY tools ./tools

# البيانات على volume مركّب في /data
CMD ["python", "-m", "marmo_sales"]
