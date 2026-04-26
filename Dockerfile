FROM node:20-slim AS dashboard-builder
WORKDIR /app/dashboard
COPY dashboard/package.json dashboard/package-lock.json* ./
RUN npm install --frozen-lockfile 2>/dev/null || npm install
COPY dashboard/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=dashboard-builder /app/dashboard/dist /app/dashboard/dist

RUN chmod +x start.sh

EXPOSE 8000

CMD ["bash", "start.sh"]
