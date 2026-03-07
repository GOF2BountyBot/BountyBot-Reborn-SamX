# AGENTS.md - blender-service

This file provides guidance for AI agents working on the blender-service.

---

## Service Overview

**blender-service** is a Python-based automation service that provides GPU-accelerated rendering and automation capabilities using Blender. It exposes a FastAPI interface for rendering jobs.

---

## Technology Stack

- **FastAPI** - Web framework
- **Blender** - 3D rendering engine (GPU-accelerated)
- **bblogger** - Logging utility (from shared library)

---

## Directory Structure

```
services/blender-service/
├── Dockerfile
├── docker-entrypoint.sh
├── requirements.txt
└── src/
    ├── main.py               # FastAPI application entry point
    ├── routers/
    │   ├── __init__.py
    │   └── health.py         # Health check endpoints
    ├── utils/
    │   ├── __init__.py
    └── lib/
        └── AEPi/            # Git submodule: https://github.com/Trimatix/AEPi.git
```

---

## Submodules

- **AEPi** - External Python library for Blender automation
  - Path: `src/lib/AEPi`
  - URL: https://github.com/Trimatix/AEPi.git
  - Initialize: `git submodule update --init --recursive`

---

## GPU Support

The service supports NVIDIA GPU rendering via:
- `docker-compose-gpu.yml` - GPU-enabled compose file
- Requires NVIDIA drivers and `nvidia-docker` runtime
- Environment variable: `NVIDIA_VISIBLE_DEVICES=all`

---

## API Endpoints

- `GET /api/v1/health/` - Health check
- Additional rendering endpoints (see `src/main.py`)

---

## Adding New Features

### Adding a New API Endpoint

1. Create a new router file in `src/routers/`
2. Register the router in `src/main.py`

---

## Health Check

- Endpoint: `GET /api/v1/health/`
- Returns service status

---

## Environment Variables

See root `.env.example`. Key variables:
- `NVIDIA_VISIBLE_DEVICES=all` - Enable GPU (optional)
- `PUID` / `PGID` - User/group IDs

---

## Docker Configuration

- Port: 8001
- Health check: `curl -s -o /dev/null -f http://localhost:8001/api/v1/health/`
- Volume mount: `./mappings/blender-renderer:/app/data`

---

*Last updated: 2026-03-07*
