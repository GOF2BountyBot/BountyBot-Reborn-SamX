import os
import traceback
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
from shared.bblogger import get_logger

flogger = get_logger("time-ann-executor")

# Point at your own /time endpoints
API_HOST = os.getenv("EXECUTOR_HOST", "bot-core")
API_PORT = os.getenv("EXECUTOR_PORT", "8000")
BASE_TIME_URL = f"http://{API_HOST}:{API_PORT}/api/v1/time"

async def execute_time_announcement_job(job_id: str, payload: dict):
    """
    Use the /time REST API to create or update a time announcement.
    - GET /time to check existence
    - POST /time to create
    - PUT /time to update
    - On first POST, modify the scheduler job to carry the new message_id.
    """
    start_ts = datetime.now(timezone.utc)
    flogger.info(f"TimeJob[{job_id}] START")
    flogger.trace(f"TimeJob[{job_id}] payload: {payload}")

    guild = payload.get("guild_id")
    channel = payload.get("channel_id")
    msg_id = payload.get("message_id")

    # 1) Check if an announcement already exists
    params = {"guild_id": guild, "channel_id": channel}
    if msg_id:
        params["message_id"] = msg_id

    # log full GET URL
    full_get_url = f"{BASE_TIME_URL}?{urlencode(params)}"
    flogger.trace(f"TimeJob[{job_id}] GET URL: {full_get_url}")

    try:
        flogger.debug(f"TimeJob[{job_id}] → GET {BASE_TIME_URL}")
        async with httpx.AsyncClient() as client:
            resp = await client.get(BASE_TIME_URL, params=params, timeout=15)
        flogger.trace(f"TimeJob[{job_id}] GET response: status={resp.status_code}, body={resp.text}")
        exists = resp.status_code == 200
    except httpx.TimeoutException as e:
        flogger.error(f"TimeJob[{job_id}] GET timeout: {e}")
        flogger.trace(traceback.format_exc())
        raise
    except httpx.HTTPError as e:
        flogger.error(f"TimeJob[{job_id}] GET error: {e}")
        flogger.trace(traceback.format_exc())
        raise

    # 2) Build request body
    now_iso = payload.get("current_time") or start_ts.isoformat()
    body = {
        "guild_id": guild,
        "channel_id": channel,
        "current_time": now_iso
    }
    method = "PUT" if exists else "POST"
    if exists:
        body["message_id"] = msg_id

    flogger.trace(f"TimeJob[{job_id}] {method} URL: {BASE_TIME_URL}")
    flogger.trace(f"TimeJob[{job_id}] {method} body: {body}")

    # 3) Create or update via REST
    try:
        flogger.debug(f"TimeJob[{job_id}] → {method} {BASE_TIME_URL}")
        async with httpx.AsyncClient() as client:
            if exists:
                r2 = await client.put(BASE_TIME_URL, json=body, timeout=30)
            else:
                r2 = await client.post(BASE_TIME_URL, json=body, timeout=30)
        flogger.trace(f"TimeJob[{job_id}] {method} response: status={r2.status_code}, body={r2.text}")
        r2.raise_for_status()
        data = r2.json()
    except httpx.TimeoutException as e:
        flogger.error(f"TimeJob[{job_id}] {method} timeout: {e}")
        flogger.trace(traceback.format_exc())
        raise
    except httpx.HTTPError as e:
        flogger.error(f"TimeJob[{job_id}] {method} error: {e}")
        flogger.trace(traceback.format_exc())
        raise
    except ValueError as e:
        flogger.error(f"TimeJob[{job_id}] JSON parse error: {e}")
        flogger.trace(f"Response text: {r2.text}")
        raise

    flogger.info(f"TimeJob[{job_id}] {method} succeeded, message_id={data.get('message_id')}")

    # 4) If first-time creation, update the job args for future PUTs
    if not exists:
        try:
            new_payload = {**payload, "message_id": data["message_id"]}
            url = f"http://{API_HOST}:{API_PORT}/api/v1/jobs/{job_id}"
            # fire off the PUT to our scheduler API
            async with httpx.AsyncClient() as client:
                await client.put(
                    url,
                    json={"payload": new_payload},   # <-- use "payload" instead of "args"
                    timeout=10,
                )
            flogger.debug(f"TimeJob[{job_id}] PUT {url} payload updated")
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"TimeJob[{job_id}] failed to update job args via API: {e}")
            flogger.trace(traceback.format_exc())
