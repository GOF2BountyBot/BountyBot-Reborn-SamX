# BountyBot-Reborn-SamX

> A containerised, **GPU-ready** micro-service stack that powers the next iteration of **BountyBot**.
> Technologies: FastAPI, PostgreSQL, CUDA, Blender, Docker-Compose, and **Discord** gateway integrations.


## Table of Contents
1. Project Layout
2. Service Overview
3. Local Development
4. Production Deployment
5. Environment Variables
6. Health-checks & Observability
7. TODOs

---

## 1. Project Layout

~~~text
BountyBot-Reborn-SamX
├── docker-compose.yml
├── .dockerignore
├── .env.example
├── README.md
├── mappings/
│   ├── postgres-data/         # Host-mounted PG volume
│   ├── bot-core/              # Persisted data for bot-core
│   └── discord-gateway/       # Persisted data for gateway
├── services/
│   ├── bot-core/
│   │   ├── Dockerfile
│   │   └── src/               # FastAPI application
│   ├── discord-gateway/
│   │   ├── Dockerfile
│   │   └── src/               # Discord bot gateway
│   └── blender-service/
│       ├── Dockerfile
│       └── src/               # Blender automation helpers
└── ...
~~~

---

## 2. Service Overview

### `db`
* **Image**: `postgres:latest`
* **Container Name**: `bounty_db`
* **Networks**: `botnetv2`
* **Volumes**
  * `/etc/localtime:/etc/localtime:ro`
  * `/etc/timezone:/etc/timezone:ro`
  * `./mappings/postgres-data:/var/lib/postgresql/data`
* **Environment**
  * `PUID` (default: `1000`)
  * `PGID` (default: `1000`)
  * `POSTGRES_USER=bounty`
  * `POSTGRES_PASSWORD=bounty`
  * `POSTGRES_DB=bountydb`
* **Health-check**: waits for PostgreSQL readiness.

---

### `bot-core`
* **Build Context**: `./`
* **Dockerfile**: `./services/bot-core/Dockerfile`
* **Container Name**: `bot-core`
* **Networks**: `botnetv2`
* **Ports**
  * `8000:8000`
* **Depends On**: `db` (healthy)
* **Volumes**
  * `/etc/localtime:/etc/localtime:ro`
  * `/etc/timezone:/etc/timezone:ro`
  * `./mappings/bot-core:/app/data`
* **Environment**
  * `PUID` / `PGID` (default: `1000`)

---
### `discord-gateway`
* **Build Context**: `./`
* **Dockerfile**: `./services/discord-gateway/Dockerfile`
* **Container Name**: `discord-gateway`
* **Networks**: `botnetv2`
* **Ports**
  * `8080:8080`
* **Depends On**: `bot-core` (healthy)
* **Volumes**
  * `/etc/localtime:/etc/localtime:ro`
  * `/etc/timezone:/etc/timezone:ro`
  * `./mappings/discord-gateway:/app/data`
* **Environment**
  * `PUID` / `PGID` (default: `1000`)

---
### `blender-service`
* **Build Context**: `./`
* **Dockerfile**: `./services/blender-service/Dockerfile`
* **Container Name**: `blender-service`
* **Networks**: `botnetv2`
* **Ports**
  * `8001:8001`
* **Environment**
  * `PUID` / `PGID` (default: `1000`)
  * `NVIDIA_VISIBLE_DEVICES=all` (optional – enable GPU)

---

## 3. Local Development
### Prerequisites
* Docker + Docker-Compose
* (Optional) NVIDIA drivers / `nvidia-docker` for GPU rendering

### Quick-start
# 1. Clone repository
   git clone https://github.com/your-repo/BountyBot-Reborn-SamX.git
   cd BountyBot-Reborn-SamX

# 2. Copy environment example
cp .env.example .env   # then edit values as required

# 3. Build & run
docker compose up --build


The stack should now be reachable on:
* `http://localhost:8000` – FastAPI docs (bot-core)
* `http://localhost:8080` – Discord gateway REST hooks (if any)

---

## 4. Production Deployment

1. Set all secrets in `.env` (or your secrets manager).
2. Map persistent volumes to durable storage.
3. Run with: docker compose -f docker-compose.yml up -d
