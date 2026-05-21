FROM python:3.12-slim

WORKDIR /app

# passlib[bcrypt] requires gcc to compile the C extension
RUN apt-get update && apt-get install -y --no-install-recommends gcc libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --no-create-home --shell /bin/false fitstorm \
    && mkdir -p /data \
    && chown fitstorm:fitstorm /data

USER fitstorm

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
