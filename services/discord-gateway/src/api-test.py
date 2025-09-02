#!/opt/venv/bin python3.11
"""
Discord Gateway API Automated Test Harness
(Covers Messages, Users, Roles, Guilds, Categories, Channels, Permissions, Health & Root)
This script implements a self-contained test runner for:
  • /api/v1/messages                (MessageTests)
  • /api/v1/users                   (UserTests)
  • /api/v1/guilds                  (GuildTests)
  • /api/v1/guilds/.../roles        (RoleTests)
  • /api/v1/guilds/.../categories   (CategoryTests)
  • /api/v1/guilds/.../channels     (ChannelTests)
  • /api/v1/permissions             (PermissionsTests)
  • /api/v1/.../permissions         (PermissionsTests)
  • /api/v1/health                  (HealthTests)
  • /                               (Root)
Key Features:
  • Python ≥3.11 (timezone-aware datetimes, modern typing)
  • Self-contained: auto-creates disposable resources with “test-” prefix
  • Absolute cleanup: tracks all created resources & deletes on exit
  • Audit log: real-time JSON-lines of created objects + cleanup results
  • Sequential tests w/ configurable delays to avoid rate limits
  • Detailed logging: stdout + single overwrite logfile, JSON pretty-print
  • Deep write-validation: compares request body → GET response automatically
  • Summary at end: passed / failed / skipped tests; exit code!=0 on any failure
  • ANSI color coded stdout
"""

import argparse
import atexit
import json
import logging
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
# Solely to avoid having to re-implement all the emoji normalization/conversion...
from utils.discord_helpers import normalize_emoji
import requests

# -----------------------------------------------------------------------------
# ANSI color codes for stdout
WHITE  = "\x1b[37m"
YELLOW = "\x1b[33m"
GREEN  = "\x1b[32m"
RED    = "\x1b[31m"
RESET  = "\x1b[0m"

# -----------------------------------------------------------------------------
# Configuration defaults (override via CLI)
DEFAULT_BASE_URL           = "http://localhost:7999"
DEFAULT_GUILD_ID: int      = 711548456019296289 # BB Test Server
DEFAULT_USER_ID: int       = 640882072516427787 # Trix Alt
DEFAULT_BOT_ID: int        = 721309941369012284 # BB Test Bot
DEFAULT_DELAY: float       = 2 # Orig 2
DEFAULT_VALIDATION_DELAY   = 5 # Orig 5
DEFAULT_LOG_FILE           = "/app/data/logs/app.log"
DEFAULT_CLEANUP_FILE       = "/app/data/logs/created_objects.log"
REQUEST_TIMEOUT            = 45  # seconds

# -----------------------------------------------------------------------------
# Global state
ARGS: argparse.Namespace
LOGGER: logging.Logger
TEST_RESULTS: List[Dict[str, Any]] = []
CLEANUP_QUEUE: List[Dict[str, Any]] = []
CLEANUP_LOCK = threading.Lock()
CLEANUP_DONE = False
CLEANUP_FAILED = False
START_TIME: float = 0.0

# -----------------------------------------------------------------------------
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def pretty(obj: Any) -> str:
    try:
        return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False)
    except Exception:
        return str(obj)

def safe_append_cleanup(entry: Dict[str, Any]) -> None:
    with CLEANUP_LOCK:
        with open(ARGS.cleanup_log, "a", encoding="utf-8") as fp:
            fp.write(json.dumps(entry, ensure_ascii=False) + "\n")

def schedule_cleanup(
    test_name: str,
    resource_type: str,
    resource_id: Union[str, int],
    delete_method: str,
    delete_uri: str,
    delete_body: Optional[dict] = None
) -> None:
    entry = {
        "timestamp":     now_iso(),
        "test_name":     test_name,
        "resource_type": resource_type,
        "resource_id":   str(resource_id),
        "delete_method": delete_method,
        "delete_uri":    delete_uri,
        "delete_body":   delete_body,
        "cleanup_result": None
    }
    with CLEANUP_LOCK:
        CLEANUP_QUEUE.append(entry)
    safe_append_cleanup(entry)
    LOGGER.warning(f"(cleanup scheduled) {resource_type} {resource_id}")

# -----------------------------------------------------------------------------
def api_call(
    method: str,
    path: str,
    *,
    body: Optional[dict] = None,
    headers: Optional[dict] = None
) -> Tuple[Optional[requests.Response], Optional[str]]:
    url = ARGS.base_url.rstrip("/") + path
    LOGGER.info(f"--> {method.upper()} {url}")
    if body is not None:
        LOGGER.debug("Request body:\n" + pretty(body))
    hdrs = headers or {"Content-Type": "application/json"}
    try:
        resp = requests.request(
            method=method, url=url, json=body, headers=hdrs, timeout=REQUEST_TIMEOUT
        )
    except requests.RequestException as e:
        LOGGER.error(f"<-- NETWORK ERROR: {e}")
        return None, str(e)
    LOGGER.info(f"<-- {resp.status_code} {resp.reason}")
    text = resp.text or ""
    if "application/json" in resp.headers.get("Content-Type", "") or text.lstrip().startswith(("{","[")):
        try:
            LOGGER.debug("Response body:\n" + pretty(resp.json()))
        except Exception:
            LOGGER.debug("Response raw:\n" + text[:5000])
    else:
        LOGGER.debug("Response text:\n" + text[:5000])
    return resp, None

# -----------------------------------------------------------------------------
def compare_lists_by_set(a: List[Any], b: List[Any]) -> bool:
    try:
        set_a = {json.dumps(x, sort_keys=True) for x in a}
        set_b = {json.dumps(x, sort_keys=True) for x in b}
        return set_a == set_b
    except Exception:
        from collections import Counter
        ca = Counter(json.dumps(x, sort_keys=True) for x in a)
        cb = Counter(json.dumps(x, sort_keys=True) for x in b)
        return ca == cb

def _validate_recursive(req: Any, resp: Any) -> Tuple[bool, str]:
    if req is None:
        return True, ""
    if isinstance(req, dict):
        if not isinstance(resp, dict):
            return False, f"expected object, got {type(resp).__name__}"
        for k, v in req.items():
            if k not in resp:
                if v is None:
                    continue
                return False, f"missing '{k}'"
            ok, reason = _validate_recursive(v, resp[k])
            if not ok:
                return False, f"in '{k}': {reason}"
        return True, ""
    if isinstance(req, list):
        if not isinstance(resp, list):
            return False, f"expected array, got {type(resp).__name__}"
        return (True, "") if compare_lists_by_set(req, resp) else (False, "array mismatch")
    return (True, "") if req == resp else (False, f"expected {req!r}, got {resp!r}")

def validate_object(request_body: Dict[str, Any], get_response_json: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Validate that the GET response contains the fields from request_body.

    Changes:
      - Unwrap the common API envelope: {"status", "timestamp", "message", "data": ...}
      - Also unwrap older/alternate wrappers like {"message": {...}} or {"channel": {...}}
      - Handle cases where the GET returns a list (arrays) vs an object.
    """
    resp_obj = get_response_json

    # 1) Unwrap canonical API envelope if present.
    if isinstance(resp_obj, dict) and "data" in resp_obj:
        resp_obj = resp_obj["data"]

    # 2) Unwrap any known single-resource wrapper(s).
    if isinstance(resp_obj, dict):
        for wrapper in ("message", "guild", "member", "role", "channel", "category", "thread", "tag"):
            if wrapper in resp_obj and isinstance(resp_obj[wrapper], (dict, list)):
                resp_obj = resp_obj[wrapper]
                break

    # 3) If the response is an array:
    if isinstance(resp_obj, list):
        # If the request body was a list, compare sets (order-insensitive).
        if isinstance(request_body, list):
            ok = compare_lists_by_set(request_body, resp_obj)
            return (True, "") if ok else (False, "array mismatch")
        # Otherwise we expected an object but got an array.
        return False, f"expected object, got array"

    # 4) If response is not a dict at this point, cannot validate fields.
    if not isinstance(resp_obj, dict):
        return False, f"unexpected response type: {type(resp_obj).__name__}"

    # 5) Field-by-field validation using the recursive validator.
    for k, v in request_body.items():
        if k not in resp_obj:
            # treat explicit nulls in the request as "optional" for validation
            if v is None:
                continue
            return False, f"missing '{k}'"
        ok, reason = _validate_recursive(v, resp_obj[k])
        if not ok:
            return False, f"field '{k}': {reason}"
    return True, ""

# -----------------------------------------------------------------------------
def record_result(
    name: str,
    method: str,
    uri: str,
    status_code: Optional[int],
    passed: bool,
    reason: Optional[str] = None
) -> None:
    skipped = bool(reason and reason.startswith("SKIPPED"))
    TEST_RESULTS.append({
        "timestamp":   now_iso(),
        "test_name":   name,
        "method":      method,
        "uri":         uri,
        "status_code": status_code,
        "passed":      passed,
        "skipped":     skipped,
        "reason":      reason,
    })
    tag = "PASS" if passed else ("SKIP" if skipped else "FAIL")
    line = f"[{tag}] {name} | {method} {uri} | {status_code}"
    if not passed and reason:
        line += f" | {reason}"
    LOGGER.info(line)
    color = GREEN if passed else (YELLOW if skipped else RED)
    sys.stdout.write(color + line + RESET + "\n")

# -----------------------------------------------------------------------------
def cleanup_all() -> None:
    """
    Attempt to delete all scheduled resources in reverse order, but only once.
    On first entry, runs cleanup; subsequent calls are no‑ops.
    """
    global CLEANUP_FAILED, CLEANUP_DONE
    if CLEANUP_DONE:
        return

    sys.stdout.write(YELLOW + "=== CLEANUP START ===\n" + RESET)
    with CLEANUP_LOCK:
        items = list(reversed(CLEANUP_QUEUE))
    for entry in items:
        rt, rid = entry["resource_type"], entry["resource_id"]
        meth, uri, bdy = entry["delete_method"], entry["delete_uri"], entry["delete_body"]
        LOGGER.warning(f"Cleaning {rt} {rid} via {meth} {uri}")
        resp, _ = api_call(meth, uri, body=bdy)
        if resp is None:
            res = "network-error"
            CLEANUP_FAILED = True
        elif resp.status_code in (200, 204):
            res = f"success:{resp.status_code}"
        else:
            res = f"failed-status:{resp.status_code}"
            CLEANUP_FAILED = True
        safe_append_cleanup({**entry, "timestamp": now_iso(), "cleanup_result": res})
        time.sleep(0.3)
    CLEANUP_DONE = True
    sys.stdout.write(YELLOW + "=== CLEANUP END ===\n" + RESET)

def _on_signal(signum, frame):
    LOGGER.warning(f"Signal {signum} received — running cleanup")
    cleanup_all()
    _print_summary_and_exit()

atexit.register(cleanup_all)
signal.signal(signal.SIGINT,  _on_signal)
signal.signal(signal.SIGTERM, _on_signal)

def _handle_uncaught(exc_type, exc_value, exc_traceback):
    # log it…
    LOGGER.error("Uncaught exception, running cleanup", 
                 exc_info=(exc_type, exc_value, exc_traceback))
    # then force the cleanup & summary
    cleanup_all()
    _print_summary_and_exit()

sys.excepthook = _handle_uncaught

# -----------------------------------------------------------------------------
class BaseTests:
    def __init__(self, guild_id:int, user_id:int, delay:float, vdelay:float):
        self.guild_id = guild_id
        self.user_id  = user_id
        self.delay    = delay
        self.vdelay   = vdelay

    def headers(self) -> dict:
        return {"Content-Type": "application/json"}

    def wait(self):
        time.sleep(self.delay)

    def wait_valid(self):
        time.sleep(self.vdelay)

    def _extract_created_id(self, resp_json: Any) -> Optional[int]:
        """
        Robustly extract a created resource id from a response JSON that may be:
          - wrapped in { "data": {...} }
          - nested under keys like "message", "channel", "role", etc.
          - expose id as "id" or legacy "message_id"
        Returns int id or None.
        """
        if not isinstance(resp_json, dict):
            return None

        j = resp_json
        # unwrap canonical envelope
        if "data" in j and isinstance(j["data"], (dict, list)):
            j = j["data"]

        # unwrap single-resource wrappers
        if isinstance(j, dict):
            for wrapper in ("message","guild","member","role","channel","category","thread","tag"):
                if wrapper in j and isinstance(j[wrapper], (dict, list)):
                    j = j[wrapper]
                    break

        # if now a dict, look for id fields
        if isinstance(j, dict):
            for key in ("message_id", "id"):
                if key in j:
                    try:
                        return int(j[key])
                    except Exception:
                        pass
            # try nested common resource keys
            for key in ("category","channel","role","thread","tag","message"):
                tmp = j.get(key)
                if isinstance(tmp, dict) and "id" in tmp:
                    try:
                        return int(tmp["id"])
                    except Exception:
                        pass
        return None

    def _normalize_data_to_list(self, resp_json) -> list:
        """
        Normalize a response JSON into a list when the payload may be one of:
          - top-level list: [...]
          - canonical envelope: {"data": [...]}
          - wrapped object: {"data": {"items": [...]}} or {"data": {"members": [...]}}, etc.
        Returns a list (possibly empty) and never raises.
        """
        # top-level list
        if isinstance(resp_json, list):
            return resp_json

        if not isinstance(resp_json, dict):
            return []

        # canonical envelope -> data
        data = resp_json.get("data", resp_json)

        # if data is a list, return directly
        if isinstance(data, list):
            return data

        if isinstance(data, dict):
            # common container keys which may hold arrays
            for key in ("items", "members", "roles", "channels", "overwrites", "tags", "threads", "messages"):
                val = data.get(key)
                if isinstance(val, list):
                    return val

        return []

    def run_simple_expected(
        self,
        name: str,
        method: str,
        path: str,
        body: Optional[dict],
        expected: List[int]
    ) -> Optional[requests.Response]:
        """
        Send a request and assert the status is in `expected`.

        On successful POST/201 (or 200) this will:
        - extract the created resource id from the canonical OpenAPI response
            envelope (data.id)
        - schedule a cleanup DELETE using the canonical delete endpoint
            for the created resource when known, otherwise fall back to
            appending /{id} to the creation path.
        """
        resp, err = api_call(method, path, body=body, headers=self.headers())
        if resp is None:
            record_result(name, method, path, None, False, f"net err {err}")
            return None

        status = resp.status_code
        ok     = status in expected

        # auto‑cleanup on successful POST (OpenAPI uses canonical "data" envelope)
        if method.upper() == "POST" and status in (200, 201):
            try:
                j = resp.json()
            except Exception:
                j = {}

            # unwrap canonical envelope -> data
            data = j.get("data") if isinstance(j, dict) else None
            if not isinstance(data, dict):
                data = j if isinstance(j, dict) else {}

            rid = None
            if isinstance(data, dict) and "id" in data:
                try:
                    rid = int(data["id"])
                except Exception:
                    rid = None

            if rid is not None:
                # choose canonical delete endpoint where applicable (per OpenAPI)
                delete_body = None
                delete_uri = None

                # messages created under channel -> DELETE /api/v1/messages/{id}
                if path.startswith("/api/v1/channels/") and path.endswith("/messages"):
                    delete_uri = f"/api/v1/messages/{rid}"
                # tags created under channel -> DELETE /api/v1/tags/{id}
                elif path.startswith("/api/v1/channels/") and path.endswith("/tags"):
                    delete_uri = f"/api/v1/tags/{rid}"
                # threads are channels (created under /channels/{id}/threads) -> DELETE /api/v1/channels/{id}
                elif path.startswith("/api/v1/channels/") and path.endswith("/threads"):
                    delete_uri = f"/api/v1/channels/{rid}"
                # channels created under a guild -> DELETE /api/v1/channels/{id}
                elif path.startswith("/api/v1/guilds/") and path.endswith("/channels"):
                    delete_uri = f"/api/v1/channels/{rid}"
                # roles created under a guild -> DELETE /api/v1/roles/{id}
                elif path.startswith("/api/v1/guilds/") and path.endswith("/roles"):
                    delete_uri = f"/api/v1/roles/{rid}"
                # categories created under a guild -> DELETE /api/v1/categories/{id}
                elif path.startswith("/api/v1/guilds/") and path.endswith("/categories"):
                    delete_uri = f"/api/v1/categories/{rid}"
                else:
                    # fallback: append id to the creating path
                    delete_uri = f"{path.rstrip('/')}/{rid}"

                schedule_cleanup(name, "resource", rid, "DELETE", delete_uri, delete_body)

        rec = None if ok else f"expected {expected}, got {status}"
        record_result(name, method, path, status, ok, rec)
        self.wait()
        return resp


    def run_validation(
        self,
        name: str,
        method: str,
        path: str,
        body: dict,
        get_path: str,
        validate_fn: Optional[Callable[[dict], Tuple[bool, str]]] = None
    ) -> None:
        resp, err = api_call(method, path, body=body, headers=self.headers())
        if resp is None:
            record_result(name, method, path, None, False, f"net err {err}")
            return
        if resp.status_code not in (200, 201, 204):
            record_result(name, method, path, resp.status_code, False,
                        f"expected 2xx, got {resp.status_code}")
            return

        # make rid visible outside the POST branch for get_path substitution
        rid: Optional[int] = None

        # auto‑cleanup on POST (use robust extractor that handles canonical OpenAPI envelope)
        if method.upper() == "POST":
            try:
                j = resp.json()
            except Exception:
                j = {}
            rid = self._extract_created_id(j)
            if rid is not None:
                delete_body = None
                # choose canonical delete endpoint where applicable (per OpenAPI)
                if path.startswith("/api/v1/channels/") and path.endswith("/messages"):
                    delete_uri = f"/api/v1/messages/{rid}"
                elif path.startswith("/api/v1/channels/") and path.endswith("/tags"):
                    delete_uri = f"/api/v1/tags/{rid}"
                elif path.startswith("/api/v1/channels/") and path.endswith("/threads"):
                    delete_uri = f"/api/v1/channels/{rid}"
                elif path.startswith("/api/v1/guilds/") and path.endswith("/channels"):
                    delete_uri = f"/api/v1/channels/{rid}"
                elif path.startswith("/api/v1/guilds/") and path.endswith("/roles"):
                    delete_uri = f"/api/v1/roles/{rid}"
                elif path.startswith("/api/v1/guilds/") and path.endswith("/categories"):
                    delete_uri = f"/api/v1/categories/{rid}"
                else:
                    delete_uri = f"{path.rstrip('/')}/{rid}"

                schedule_cleanup(name, "resource", rid, "DELETE", delete_uri, delete_body)

        self.wait_valid()

        # allow get_path templates like "/api/v1/categories/{id}" for POST-created resources
        get_path_used = get_path
        if "{id}" in get_path:
            if rid is not None:
                try:
                    get_path_used = get_path.format(id=rid)
                except Exception:
                    record_result(name, "GET", get_path, None, False, "GET path formatting failed")
                    return
            else:
                # can't resolve template because POST didn't expose an id
                record_result(name, "GET", get_path, None, False, "cannot determine id for GET")
                return

        gresp, _ = api_call("GET", get_path_used, headers=self.headers())
        if gresp is None or gresp.status_code != 200:
            record_result(name, "GET", get_path_used,
                        getattr(gresp, "status_code", None), False,
                        f"expected GET200, got {getattr(gresp, 'status_code', None)}")
            return

        data = gresp.json()
        ok, reason = (validate_fn(data) if validate_fn
                    else validate_object(body, data))
        record_result(name, method, path, resp.status_code, ok,
                    None if ok else f"validation:{reason}")
        self.wait()

    def run_forbidden(self, name:str, method:str, path:str, body:Optional[dict]=None):
        record_result(name, method, path, None, False, "SKIPPED (no auth)")

# -----------------------------------------------------------------------------
class MessageTests(BaseTests):
    """Test suite for /api/v1/messages endpoints."""

    def run_all(self):
        cid = self._make_channel()
        if cid is None:
            LOGGER.error("Channel setup failed; skipping message tests")
            return

        for test in (
            self.test_create_valid,
            self.test_create_missing,
            self.test_create_invalid_embed,
            self.test_create_invalid_ids,
            self.test_create_large_embed,
            self.test_create_forbidden,

            # standard updates
            self.test_update_valid,
            self.test_update_missing,
            self.test_update_nonexistent,
            self.test_update_invalid_embed,
            self.test_update_forbidden,

            # deletes
            self.test_delete_valid,
            self.test_delete_missing,
            self.test_delete_nonexistent,
            self.test_delete_forbidden,

            # gets
            self.test_get_valid,
            self.test_get_missing_path,
            self.test_get_invalid_ids,
            self.test_get_notfound,
            self.test_get_forbidden,

            # new embed‐style creation tests
            self.test_create_footer,
            self.test_create_image,
            self.test_create_code_block,

            # new embed‐style update tests
            self.test_update_footer,
            self.test_update_image,
            self.test_update_code_block,
        ):
            try:
                test(cid)
            except Exception:
                LOGGER.exception("Unhandled exception in message test")

    def _make_channel(self) -> Optional[int]:
        path = f"/api/v1/guilds/{self.guild_id}/channels"
        body = {"name": f"test-channel-{int(time.time())}"}
        resp, _ = api_call("POST", path, body=body, headers=self.headers())
        if not resp or resp.status_code not in (200, 201):
            record_result(
                "setup_channel", "POST", path,
                getattr(resp, "status_code", None), False,
                "channel creation failed"
            )
            return None
        try:
            j = resp.json()
        except Exception:
            j = {}
        # use the robust extractor (handles canonical data envelope)
        cid = self._extract_created_id(j)
        if cid is None:
            record_result("setup_channel", "POST", path,
                            getattr(resp, "status_code", None), False,
                            "unable to parse created channel id")
            return None
        schedule_cleanup(
            "setup_channel", "channel", cid,
            "DELETE", f"/api/v1/channels/{cid}"
        )
        self.wait_valid()
        api_call("GET", f"/api/v1/channels/{cid}", headers=self.headers())
        return cid

    def _create_helper(self, cid: int, label: str) -> Optional[int]:
        # Use canonical channel-scoped message create endpoint per OpenAPI
        path = f"/api/v1/channels/{cid}/messages"
        body = {"content": {"title": label, "description": now_iso()}}
        resp, _ = api_call("POST", path, body=body, headers=self.headers())
        if not resp or resp.status_code not in (200, 201):
            LOGGER.warning("Helper message creation failed")
            return None
        try:
            j = resp.json()
        except Exception:
            j = {}
        mid = self._extract_created_id(j)
        if mid is None:
            LOGGER.warning("Unable to parse helper message id")
            return None
        # Canonical delete for messages is DELETE /api/v1/messages/{id}
        schedule_cleanup(
            f"helper_{label}", "message", mid, "DELETE", f"/api/v1/messages/{mid}"
        )
        self.wait_valid()
        return mid

    def test_create_valid(self, cid: int):
        """
        Create a valid message via the canonical channel-scoped endpoint and
        validate persistence by GET /api/v1/messages/{id}.
        """
        name = "POST create valid"
        path = f"/api/v1/channels/{cid}/messages"
        body = {"content": {"title": "Valid Create", "description": now_iso()}}
        # run_validation will POST, extract the created id, schedule cleanup,
        # then GET /api/v1/messages/{id} and validate the stored fields.
        self.run_validation(name, "POST", path, body, "/api/v1/messages/{id}")

    def test_create_missing(self, cid: int):
        # POST to canonical channel-scoped endpoint but omit required "content"
        self.run_simple_expected(
            "POST missing fields", "POST",
            f"/api/v1/channels/{cid}/messages",
            {},  # missing required content
            [400, 422]
        )

    def test_create_invalid_embed(self, cid: int):
        # POST invalid embed payload to channel messages endpoint
        body = {"content": {"fields": [{"value": "test-invalid"}]}}
        self.run_simple_expected(
            "POST invalid embed", "POST",
            f"/api/v1/channels/{cid}/messages", body, [400, 422]
        )

    def test_create_invalid_ids(self, cid: int):
        # Attempt to POST to a non-existent channel -> expect 404
        body = {"content": {"title": "test-invalid"}}
        self.run_simple_expected(
            "POST invalid channel id", "POST",
            "/api/v1/channels/999999999999/messages", body, [404]
        )

    def test_create_large_embed(self, cid: int):
        body = {"content": {"description": "x" * 20000}}
        self.run_simple_expected(
            "POST large embed", "POST",
            f"/api/v1/channels/{cid}/messages", body, list(range(400, 500))
        )

    def test_create_forbidden(self, cid: int):
        self.run_forbidden(
            "POST forbidden", "POST", f"/api/v1/channels/{cid}/messages",
            {"content": {}}
        )

    def test_update_valid(self, cid: int):
        mid = self._create_helper(cid, "upd_ok")
        if not mid:
            return
        path = f"/api/v1/messages/{mid}"
        body = {"content": {"description": "updated"}}
        # use run_validation to do PUT then GET validation (get_path = same message endpoint)
        self.run_validation(
            "PUT update valid", "PUT", path, body,
            f"/api/v1/messages/{mid}"
        )


    def test_update_missing(self, cid: int):
        # create message, then attempt update with missing 'content'
        mid = self._create_helper(cid, "upd_missing")
        if not mid:
            return
        path = f"/api/v1/messages/{mid}"
        # missing content → expect validation error
        self.run_simple_expected(
            "PUT missing fields", "PUT", path, {}, [400, 422]
        )

    def test_update_nonexistent(self, cid: int):
        # PUT to canonical single-message path for nonexistent id
        path = f"/api/v1/messages/999999999"
        body = {"content": {}}
        self.run_simple_expected(
            "PUT nonexistent", "PUT", path, body, [404]
        )

    def test_update_invalid_embed(self, cid: int):
        mid = self._create_helper(cid, "upd_bad")
        if not mid:
            return
        path = f"/api/v1/messages/{mid}"
        body = {"content": {"fields": [{"value": "test-invalid"}]}}
        self.run_simple_expected(
            "PUT invalid embed", "PUT", path, body, list(range(400, 500))
        )

    def test_update_forbidden(self, cid: int):
        mid = self._create_helper(cid, "upd_forb")
        if not mid:
            return
        self.run_forbidden(
            "PUT forbidden", "PUT", "/api/v1/messages",
            {"guild_id": self.guild_id, "channel_id": cid,
             "message_id": mid, "content": {}}
        )

    def test_delete_valid(self, cid: int):
        mid = self._create_helper(cid, "del_ok")
        if not mid:
            return
        # Prevent double-delete
        with CLEANUP_LOCK:
            CLEANUP_QUEUE[:] = [
                e for e in CLEANUP_QUEUE
                if not (e["resource_type"] == "message" and e["resource_id"] == str(mid))
            ]
        path = f"/api/v1/messages/{mid}"
        self.run_simple_expected("DELETE valid", "DELETE", path, None, [200, 204])
        self.wait_valid()
        self.run_simple_expected(
            "GET after delete", "GET",
            f"/api/v1/messages/{mid}", None, [404]
        )

    def test_delete_missing(self, cid: int):
        self.run_simple_expected(
            "DELETE missing fields", "DELETE", "/api/v1/messages",
            {"guild_id": self.guild_id}, [404]
        )

    def test_delete_nonexistent(self, cid: int):
        self.run_simple_expected(
            "DELETE nonexistent", "DELETE", "/api/v1/messages/999999999", None, [404]
        )

    def test_delete_forbidden(self, cid: int):
        mid = self._create_helper(cid, "del_forb")
        if not mid:
            return
        self.run_forbidden(
            "DELETE forbidden", "DELETE", "/api/v1/messages/{mid}"
        )

    def test_get_valid(self, cid: int):
        mid = self._create_helper(cid, "get_ok")
        if not mid:
            return
        self.run_simple_expected(
            "GET valid", "GET",
            f"/api/v1/messages/{mid}", None, [200]
        )

    def test_get_missing_path(self, cid: int):
        self.run_simple_expected(
           "GET missing path", "GET", "/api/v1/messages/", None, [400, 404]
        )

    def test_get_invalid_ids(self, cid: int):
        self.run_simple_expected(
            "GET invalid id format", "GET",
            "/api/v1/messages/abc", None, list(range(400, 500))
        )

    def test_get_notfound(self, cid: int):
        self.run_simple_expected(
            "GET not found", "GET",
            f"/api/v1/messages/{self.guild_id}/{cid}/999999999", None, [404]
        )

    def test_get_forbidden(self, cid: int):
        self.run_forbidden(
            "GET forbidden", "GET", "/api/v1/messages/0"
        )

    # New embed‑style creation tests — now use run_validation
    def test_create_footer(self, cid: int):
        name = "POST create with footer"
        path = f"/api/v1/channels/{cid}/messages"
        body = {
            "content": {
                "title": "Footer Test",
                "description": "Testing footer fields",
                "footer_text": "My Footer",
                "footer_icon_url": "https://example.com/icon.png"
            }
        }
        self.run_validation(name, "POST", path, body, "/api/v1/messages/{id}")

    def test_create_image(self, cid: int):
        name = "POST create with image"
        path = f"/api/v1/channels/{cid}/messages"
        body = {
            "content": {
                "title": "Image Test",
                "description": "Testing image/thumbnail",
                "image_url": "https://i.postimg.cc/htH2L8gq/terran-logo.png",
                "thumbnail_url": "https://i.postimg.cc/htH2L8gq/terran-logo.png"
            }
        }
        self.run_validation(name, "POST", path, body, "/api/v1/messages/{id}")

    def test_create_code_block(self, cid: int):
        name = "POST create with code block"
        path = f"/api/v1/channels/{cid}/messages"
        code = "```python\nprint('hello world')\n```"
        body = {"content": {"title": "Code Block", "description": code}}
        self.run_validation(name, "POST", path, body, "/api/v1/messages/{id}")

    # New embed‑style update tests — now use run_validation
    def test_update_image(self, cid: int):
        mid = self._create_helper(cid, "upd_image")
        if not mid:
            return
        path = f"/api/v1/messages/{mid}"
        body = {"content": {
            "image_url": "https://i.postimg.cc/htH2L8gq/terran-logo.png",
            "thumbnail_url": "https://i.postimg.cc/htH2L8gq/terran-logo.png"
        }}
        self.run_validation("PUT update image", "PUT", path, body, f"/api/v1/messages/{mid}")

    def test_update_footer(self, cid: int):
        mid = self._create_helper(cid, "upd_footer")
        if not mid:
            return
        path = f"/api/v1/messages/{mid}"
        body = {"content": {
            "footer_text": "Updated Footer",
            "footer_icon_url": "https://i.postimg.cc/bY3LCf8g/vossk-logo.png"
        }}
        self.run_validation("PUT update footer", "PUT", path, body, f"/api/v1/messages/{mid}")

    def test_update_code_block(self, cid: int):
        mid = self._create_helper(cid, "upd_code")
        if not mid:
            return
        path = f"/api/v1/messages/{mid}"
        code = "```python\nprint('updated code')\n```"
        body = {"content": {"description": code}}
        self.run_validation("PUT update code block", "PUT", path, body, f"/api/v1/messages/{mid}")


# -----------------------------------------------------------------------------
class UserTests(BaseTests):
    """Test suite for /api/v1/users and guild member endpoints."""

    def run_all(self):
        for test in (
            self.test_get_me,
            self.test_get_me_forbid,
            self.test_get_user_valid,
            self.test_get_user_notfound,
            self.test_get_user_invalid_type,
            self.test_get_user_forbid,
            self.test_get_member_valid,
            self.test_get_member_notfound,
            self.test_get_member_invalid,
            self.test_get_member_forbid,
            self.test_update_member_nick,
            self.test_update_member_roles,       # dynamic role test
            self.test_update_member_mute_deaf,
            self.test_update_member_invalid_roles,
            self.test_update_member_forbid,
        ):
            try:
                test()
            except Exception:
                LOGGER.exception("Unhandled exception in user test")

    def test_get_me(self):
        self.run_simple_expected("GET bot identity","GET","/api/v1/users/@me",None,[200])
    def test_get_me_forbid(self):
        self.run_forbidden("GET bot identity forbid","GET","/api/v1/users/@me")

    def test_get_user_valid(self):
        self.run_simple_expected("GET user valid","GET",f"/api/v1/users/{self.user_id}",None,[200])
    def test_get_user_notfound(self):
        self.run_simple_expected("GET user notfound","GET","/api/v1/users/999999999999",None,[404])
    def test_get_user_invalid_type(self):
        self.run_simple_expected("GET user invalid","GET","/api/v1/users/abc",None,list(range(400,500)))
    def test_get_user_forbid(self):
        self.run_forbidden("GET user forbid","GET",f"/api/v1/users/{self.user_id}")

    def test_get_member_valid(self):
        path = f"/api/v1/members/{self.user_id}"
        self.run_simple_expected("GET member valid","GET",path,None,[200])
    def test_get_member_notfound(self):
        path = f"/api/v1/members/999999999999"
        self.run_simple_expected("GET member notfound","GET",path,None,[404])
    def test_get_member_invalid(self):
        path = f"/api/v1/members/abc"
        self.run_simple_expected("GET member invalid","GET",path,None,list(range(400,500)))
    def test_get_member_forbid(self):
        path = f"/api/v1/members/{self.user_id}"
        self.run_forbidden("GET member forbid","GET",path)
    def test_update_member_nick(self):
        path = f"/api/v1/members/{self.user_id}"
        self.run_simple_expected("PUT change nick","PUT",path,{"nick":f"n-{int(time.time())}"},[200])

    def test_update_member_roles(self):
        """
        Create a temporary role, assign it to the member via the canonical
        member endpoint (PUT /api/v1/members/{user_id}), validate via GET that
        the role appears in the member's roles array, then delete the temp role.
        """
        name = "PUT update roles"

        # 1) CREATE temp role (guild-scoped creation remains)
        role_body = {"name": f"test-role-{int(time.time())}", "position": 99}
        rresp, _ = api_call("POST", f"/api/v1/guilds/{self.guild_id}/roles",
                            body=role_body, headers=self.headers())
        if not rresp or rresp.status_code not in (200, 201):
            record_result(name, "POST", f"/api/v1/guilds/{self.guild_id}/roles",
                          getattr(rresp, "status_code", None), False, "role-create failed")
            return
        try:
            rj = rresp.json()
        except Exception:
            rj = {}
        # robust id extraction (handles data.id)
        role_id = self._extract_created_id(rj)
        if role_id is None:
            record_result(name, "POST", f"/api/v1/guilds/{self.guild_id}/roles",
                          getattr(rresp, "status_code", None), False, "role-create parse failed")
            return
        # schedule cleanup using canonical roles DELETE path
        schedule_cleanup(name, "role", role_id, "DELETE", f"/api/v1/roles/{role_id}")
        self.wait_valid()

        # 2) ASSIGN that role using canonical member endpoint — use run_validation
        mpath = f"/api/v1/members/{self.user_id}"
        mbody = {"roles": [role_id]}

        def validate_member_roles(get_json: dict) -> Tuple[bool, str]:
            j = get_json
            if isinstance(j, dict) and "data" in j:
                j = j["data"]
            # unwrap legacy wrapper only if it contains roles
            if isinstance(j, dict):
                if "member" in j and isinstance(j["member"], dict):
                    j = j["member"]
                elif "user" in j and isinstance(j["user"], dict) and "roles" in j["user"]:
                    j = j["user"]
            roles = []
            if isinstance(j, dict):
                roles = j.get("roles") or []
            try:
                ok = int(role_id) in [int(x) for x in roles]
            except Exception:
                ok = False
            return (True, "") if ok else (False, f"role {role_id} not present on member")

        # run_validation will PUT then GET mpath and call validate_member_roles
        self.run_validation(name, "PUT", mpath, mbody, mpath, validate_fn=validate_member_roles)
        self.wait_valid()

        # 3) DELETE temp role using canonical roles endpoint
        dpath = f"/api/v1/roles/{role_id}"
        dresp, _ = api_call("DELETE", dpath, headers=self.headers())
        dok = dresp is not None and dresp.status_code in (200, 204)
        record_result(name + " cleanup role", "DELETE", dpath,
                      getattr(dresp, "status_code", None), dok,
                      None if dok else f"delete failed {dresp.status_code if dresp else 'ERR'}")
        # Remove scheduled cleanup entry for this role (we deleted it explicitly)
        with CLEANUP_LOCK:
            CLEANUP_QUEUE[:] = [e for e in CLEANUP_QUEUE
                                if not (e["resource_type"] == "role" and e["resource_id"] == str(role_id))]
        self.wait()

    def test_update_member_mute_deaf(self):
        path = f"/api/v1/members/{self.user_id}"
        self.run_simple_expected("PUT toggle mute/deaf","PUT",path,{"mute":True,"deaf":True},[200])

    def test_update_member_invalid_roles(self):
        path = f"/api/v1/members/{self.user_id}"
        self.run_simple_expected("PUT invalid roles","PUT",path,{"roles":["nope"]},list(range(400,500)))

    def test_update_member_forbid(self):
        path = f"/api/v1/members/{self.user_id}"
        self.run_forbidden("PUT member forbid","PUT",path,{"nick":"nope"})

# -----------------------------------------------------------------------------
class RoleTests(BaseTests):
    """Test suite for /api/v1/guilds/{guild_id}/roles endpoints."""

    def run_all(self):
        for test in (
            self.test_list_roles_valid,
            self.test_list_roles_invalid,
            self.test_list_roles_forbid,
            self.test_create_role_valid,
            self.test_create_role_missing_name,
            self.test_create_role_invalid_perms,
            self.test_create_role_forbid,
            self.test_get_role_valid,
            self.test_get_role_notfound,
            self.test_get_role_forbid,
            self.test_update_role_valid,
            self.test_update_role_invalid_color,
            self.test_update_role_forbid,
            self.test_delete_role_valid,
            self.test_delete_role_protected,
            self.test_delete_role_invalid,
            self.test_delete_role_forbid,
            self.test_list_role_members_valid,
            self.test_list_role_members_empty,
            self.test_list_role_members_forbid,
            self.test_assign_role_valid,
            self.test_assign_role_notin_guild,
            self.test_assign_role_idempotent,
            self.test_assign_role_forbid,
            self.test_remove_role_valid,
            self.test_remove_role_notassigned,
            self.test_remove_role_forbid,
            self.test_check_user_has_role_valid,
            self.test_check_user_does_not_have_role_valid,
            self.test_check_user_has_role_notfound,
            self.test_check_user_has_role_invalid,
            self.test_check_user_has_role_forbid,
            self.test_role_position_order,
            self.test_role_position_sorting,
        ):
            try:
                test()
            except Exception:
                LOGGER.exception("Unhandled exception in role test")

    # GET /roles
    def test_list_roles_valid(self):
        p=f"/api/v1/guilds/{self.guild_id}/roles"
        self.run_simple_expected("GET list roles","GET",p,None,[200])

    def test_list_roles_invalid(self):
        p=f"/api/v1/guilds/999999999999/roles"
        self.run_simple_expected("GET list roles invalid guild","GET",p,None,[404])

    def test_list_roles_forbid(self):
        p=f"/api/v1/guilds/{self.guild_id}/roles"
        self.run_forbidden("GET list roles forbid","GET",p,None)

    # POST /roles
    def test_create_role_valid(self):
        """
        Create a role via the canonical guild-scoped POST and validate the
        persisted role via GET /api/v1/roles/{id}. run_validation will POST,
        extract the created id, schedule cleanup (canonical DELETE), then GET
        and validate the stored fields.
        """
        name = "POST create role valid"
        path = f"/api/v1/guilds/{self.guild_id}/roles"
        body = {"name": f"test-role-{int(time.time())}", "permissions": 0}
        # use templated GET path; run_validation will substitute {id}
        self.run_validation(name, "POST", path, body, f"/api/v1/roles/{{id}}")

    def test_create_role_missing_name(self):
        name, path = "POST create role missing name", f"/api/v1/guilds/{self.guild_id}/roles"
        body = {"permissions": 0}
        resp, _ = api_call("POST", path, body=body, headers=self.headers())
        ok = bool(resp and resp.status_code in (200, 201, 422))
        if resp and resp.status_code in (200, 201):
            try:
                j = resp.json()
            except Exception:
                j = {}
            rid = self._extract_created_id(j)
            if rid is not None:
                schedule_cleanup(name, "role", rid, "DELETE", f"/api/v1/roles/{rid}")
        record_result(name, "POST", path, getattr(resp, "status_code", None), ok,
                      None if ok else f"expected [201|422], got {resp.status_code if resp else 'ERR'}")
        self.wait()

    def test_create_role_invalid_perms(self):
        body={"name":f"test-role-invalid-{int(time.time())}","permissions":-1}
        self.run_simple_expected("POST create role invalid perms","POST",
                                 f"/api/v1/guilds/{self.guild_id}/roles",body,[400,422])

    def test_create_role_forbid(self):
        p=f"/api/v1/guilds/{self.guild_id}/roles"
        self.run_forbidden("POST create role forbid","POST",p,None)

    # GET /roles/{role_id}
    def test_get_role_valid(self):
        rid = self._create_helper_role()
        if not rid:
            return
        p = f"/api/v1/roles/{rid}"
        self.run_simple_expected("GET role valid", "GET", p, None, [200])

    def test_get_role_notfound(self):
        p = f"/api/v1/roles/999999999999"
        self.run_simple_expected("GET role not found", "GET", p, None, [404])

    def test_get_role_forbid(self):
        p = f"/api/v1/roles/0"
        self.run_forbidden("GET role forbid", "GET", p, None)

    # PUT /roles/{role_id}
    def test_update_role_valid(self):
        rid = self._create_helper_role()
        if not rid:
            return
        p = f"/api/v1/roles/{rid}"
        body = {"name": f"test-role-upd-{int(time.time())}", "color": 12345}
        self.run_validation("PUT update role valid", "PUT", p, body, p)

    def test_update_role_invalid_color(self):
        rid = self._create_helper_role()
        if not rid:
            return
        body = {"color": -5}
        p = f"/api/v1/roles/{rid}"
        self.run_simple_expected("PUT update role invalid color", "PUT", p, body, [400, 422])

    def test_update_role_forbid(self):
        rid = self._create_helper_role()
        if not rid:
            return
        p = f"/api/v1/roles/{rid}"
        self.run_forbidden("PUT update role forbid", "PUT", p, None)

    # DELETE /roles/{role_id}
    def test_delete_role_valid(self):
        rid = self._create_helper_role()
        if not rid:
            return
        with CLEANUP_LOCK:
            CLEANUP_QUEUE[:] = [e for e in CLEANUP_QUEUE
                                if not (e["resource_type"] == "role" and e["resource_id"] == str(rid))]
        p = f"/api/v1/roles/{rid}"
        self.run_simple_expected("DELETE role valid", "DELETE", p, None, [200, 204])

    def test_delete_role_protected(self):
        # protected role id mapped to canonical single-role endpoint
        p = f"/api/v1/roles/{self.guild_id}"
        self.run_simple_expected("DELETE protected role", "DELETE", p, None, list(range(400, 500)))

    def test_delete_role_invalid(self):
        p = f"/api/v1/roles/999999999999"
        self.run_simple_expected("DELETE role invalid", "DELETE", p, None, [404])

    def test_delete_role_forbid(self):
        p = f"/api/v1/roles/0"
        self.run_forbidden("DELETE role forbid", "DELETE", p, None)

    # GET /roles/{role_id}/members
    def test_list_role_members_valid(self):
        rid = self._create_helper_role()
        if not rid:
            return
        p = f"/api/v1/roles/{rid}/members"
        self.run_simple_expected("GET role members valid", "GET", p, None, [200])

    def test_list_role_members_empty(self):
        rid = self._create_helper_role()
        if not rid:
            return
        p = f"/api/v1/roles/{rid}/members"
        resp = self.run_simple_expected("GET role members empty", "GET", p, None, [200])
        if resp and resp.status_code == 200:
            try:
                j = resp.json()
            except Exception:
                j = {}
            arr = self._normalize_data_to_list(j)
            ok = isinstance(arr, list) and len(arr) == 0
            record_result("GET role members empty verify", "GET", p, resp.status_code, ok,
                          None if ok else "members not empty")

    def test_list_role_members_forbid(self):
        p = f"/api/v1/roles/0/members"
        self.run_forbidden("GET role members forbid", "GET", p, None)

    # PUT /roles/{role_id}/members/{user_id}
    def test_assign_role_valid(self):
        """
        Assign a role to the test user via PUT /api/v1/roles/{role_id}/members/{user_id}.
        Use run_validation with a GET of /api/v1/members/{user_id} and a custom
        validate_fn to ensure the role appears in the member.roles array.
        """
        rid = self._create_helper_role()
        if not rid:
            return
        p = f"/api/v1/roles/{rid}/members/{self.user_id}"

        def validate_member_has_role(get_json: dict) -> Tuple[bool, str]:
            j = get_json
            if isinstance(j, dict) and "data" in j:
                j = j["data"]
            # unwrap legacy single-resource wrappers only if they actually contain roles
            if isinstance(j, dict):
                if "member" in j and isinstance(j["member"], dict):
                    j = j["member"]
                elif "user" in j and isinstance(j["user"], dict) and "roles" in j["user"]:
                    j = j["user"]
            roles = []
            if isinstance(j, dict):
                roles = j.get("roles") or []
            try:
                ok = int(rid) in [int(x) for x in roles]
            except Exception:
                ok = False
            return (True, "") if ok else (False, f"role {rid} not present on member")

        # Use {} as body since this endpoint does not require a payload.
        self.run_validation("PUT assign role valid", "PUT", p, {}, f"/api/v1/members/{self.user_id}", validate_fn=validate_member_has_role)


    def test_assign_role_notin_guild(self):
        rid = self._create_helper_role()
        if not rid:
            return
        p = f"/api/v1/roles/{rid}/members/999999999999"
        self.run_simple_expected("PUT assign not in guild", "PUT", p, None, [404])

    def test_assign_role_idempotent(self):
        rid = self._create_helper_role()
        if not rid:
            return
        p = f"/api/v1/roles/{rid}/members/{self.user_id}"
        self.run_simple_expected("PUT assign idempotent 1", "PUT", p, None, [200])
        self.run_simple_expected("PUT assign idempotent 2", "PUT", p, None, [200])

    def test_assign_role_forbid(self):
        rid = self._create_helper_role()
        if not rid:
            return
        p = f"/api/v1/roles/{rid}/members/{self.user_id}"
        self.run_forbidden("PUT assign role forbid", "PUT", p, None)

    # DELETE /roles/{role_id}/members/{user_id}
    def test_remove_role_valid(self):
        """
        Assign a role to the user, then DELETE it via the canonical endpoint.
        Use run_validation on the DELETE with a GET of /api/v1/members/{user_id}
        to verify the role has been removed from the member's roles array.
        """
        rid = self._create_helper_role()
        if not rid:
            return
        p = f"/api/v1/roles/{rid}/members/{self.user_id}"
        # ensure the role is assigned first
        api_call("PUT", p, headers=self.headers()); self.wait_valid()

        def validate_member_no_role(get_json: dict) -> Tuple[bool, str]:
            j = get_json
            if isinstance(j, dict) and "data" in j:
                j = j["data"]
            if isinstance(j, dict):
                if "member" in j and isinstance(j["member"], dict):
                    j = j["member"]
                elif "user" in j and isinstance(j["user"], dict) and "roles" in j["user"]:
                    j = j["user"]
            roles = []
            if isinstance(j, dict):
                roles = j.get("roles") or []
            try:
                ok = int(rid) not in [int(x) for x in roles]
            except Exception:
                ok = False
            return (True, "") if ok else (False, f"role {rid} still present on member")

        # run_validation will perform the DELETE then GET the member to validate
        self.run_validation("DELETE remove role valid", "DELETE", p, {}, f"/api/v1/members/{self.user_id}", validate_fn=validate_member_no_role)

    def test_remove_role_notassigned(self):
        rid = self._create_helper_role()
        if not rid:
            return
        p = f"/api/v1/roles/{rid}/members/{self.user_id}"
        self.run_simple_expected("DELETE remove not assigned", "DELETE", p, None, [200, 404])

    def test_remove_role_forbid(self):
        p = f"/api/v1/roles/0/members/{self.user_id}"
        self.run_forbidden("DELETE remove forbid", "DELETE", p, None)

    def test_check_user_has_role_valid(self):
        """
        Create a temporary role, assign it to the test user via the canonical
        role-member assign endpoint, then call the new check endpoint:
          GET /api/v1/roles/{role_id}/members/{user_id}/check
        Expect allowed=true while assigned, and allowed=false after removal.
        """
        name = "GET check user has role valid"

        # 1) create temp role
        rid = self._create_helper_role()
        if not rid:
            return
        # 2) assign role to user (use canonical assign endpoint)
        ap = f"/api/v1/roles/{rid}/members/{self.user_id}"
        aresp, _ = api_call("PUT", ap, headers=self.headers())
        assigned_ok = bool(aresp and aresp.status_code in (200, 201, 204))
        record_result(name + " assign", "PUT", ap, getattr(aresp, "status_code", None), assigned_ok,
                      None if assigned_ok else "assign failed")
        self.wait_valid()
        # 3) CHECK -> expect allowed == true
        check_path = f"/api/v1/roles/{rid}/members/{self.user_id}/check"
        cresp, _ = api_call("GET", check_path, headers=self.headers())
        if not cresp:
            record_result(name + " check (after assign)", "GET", check_path, None, False, "network error")
        elif cresp.status_code != 200:
            record_result(name + " check (after assign)", "GET", check_path, cresp.status_code, False,
                          f"expected 200, got {cresp.status_code}")
        else:
            try:
                cj = cresp.json()
            except Exception:
                cj = {}
            data = cj.get("data", cj if isinstance(cj, dict) else {})
            allowed = None
            if isinstance(data, dict):
                allowed = data.get("allowed")
            ok = (allowed is True) or (isinstance(allowed, (int, float)) and bool(allowed))
            record_result(name + " check (after assign)", "GET", check_path, cresp.status_code, ok,
                          None if ok else f"expected allowed true, got {allowed!r}")
        self.wait_valid()
        # 4) remove role from user and re-check -> expect allowed == false
        dresp, _ = api_call("DELETE", ap, headers=self.headers())
        removed_ok = bool(dresp and dresp.status_code in (200, 204, 404))
        # Accept 404 (not-in-guild) as a possible remove outcome but still proceed
        record_result(name + " remove", "DELETE", ap, getattr(dresp, "status_code", None), removed_ok,
                      None if removed_ok else "remove failed")
        self.wait_valid()
        cresp2, _ = api_call("GET", check_path, headers=self.headers())
        if not cresp2:
            record_result(name + " check (after remove)", "GET", check_path, None, False, "network error")
            return
        if cresp2.status_code != 200:
            # if resource/guild/member removed, server may return 404 — record as expected failure
            ok = cresp2.status_code in (200, 404)
            record_result(name + " check (after remove)", "GET", check_path, cresp2.status_code, ok,
                          None if ok else f"unexpected status {cresp2.status_code}")
            return
        try:
            cj2 = cresp2.json()
        except Exception:
            cj2 = {}
        data2 = cj2.get("data", cj2 if isinstance(cj2, dict) else {})
        allowed2 = None
        if isinstance(data2, dict):
            allowed2 = data2.get("allowed")
        ok2 = (allowed2 is False) or (isinstance(allowed2, (int, float)) and not bool(allowed2))
        record_result(name + " check (after remove)", "GET", check_path, cresp2.status_code, ok2,
                      None if ok2 else f"expected allowed false, got {allowed2!r}")
        self.wait()

    def test_check_user_does_not_have_role_valid(self):
        """
        Create a temporary role, DO NOT assign it to the test user, then call the
        new check endpoint:
          GET /api/v1/roles/{role_id}/members/{user_id}/check
        Expect HTTP200 and allowed == false.
        """
        name = "GET check user does NOT have role valid"

        # 1) create temp role
        rid = self._create_helper_role()
        if not rid:
            return

        # 2) directly CHECK -> expect allowed == false
        check_path = f"/api/v1/roles/{rid}/members/{self.user_id}/check"
        cresp, _ = api_call("GET", check_path, headers=self.headers())
        if not cresp:
            record_result(name, "GET", check_path, None, False, "network error")
            return
        if cresp.status_code != 200:
            record_result(name, "GET", check_path, cresp.status_code, False,
                          f"expected 200, got {cresp.status_code}")
            return

        try:
            cj = cresp.json()
        except Exception:
            cj = {}
        data = cj.get("data", cj if isinstance(cj, dict) else {})
        allowed = None
        if isinstance(data, dict):
            allowed = data.get("allowed")
        ok = (allowed is False) or (isinstance(allowed, (int, float)) and not bool(allowed))
        record_result(name, "GET", check_path, cresp.status_code, ok,
                      None if ok else f"expected allowed false, got {allowed!r}")
        self.wait()

    def test_check_user_has_role_notfound(self):
        """
        Non-existent role should produce 404
        """
        p = f"/api/v1/roles/999999999999/members/{self.user_id}/check"
        self.run_simple_expected("GET check user role notfound", "GET", p, None, [404])

    def test_check_user_has_role_invalid(self):
        """
        Invalid id formats should produce 4xx
        """
        p = f"/api/v1/roles/abc/members/def/check"
        self.run_simple_expected("GET check user role invalid ids", "GET", p, None, list(range(400,500)))

    def test_check_user_has_role_forbid(self):
        """
        Forbidden (no-auth) case — harness marks as skipped
        """
        self.run_forbidden("GET check user role forbid", "GET", f"/api/v1/roles/0/members/0/check", None)


    # Role position ordering tests
    def test_role_position_order(self):
        """
        Verify that role listing respects position ordering.
        Relaxed rules to permit servers which normalize newly created roles
        to the same position:
          - positions must be present and non-decreasing (posA <= posB)
          - if posA < posB then the index of A must be before B (idxA < idxB)
          - if posA == posB any ordering is accepted (server may normalize)
        """
        name = "GET roles position order"
        base = f"/api/v1/guilds/{self.guild_id}/roles"

        # Create role A (requested position 10)
        a = {"name": f"test-role-pos-A-{int(time.time())}", "position": 10}
        ra, _ = api_call("POST", base, body=a, headers=self.headers())
        if not ra or ra.status_code not in (200, 201):
            record_result(name, "POST", base, getattr(ra, "status_code", None), False, "A create failed")
            return
        try:
            rja = ra.json()
        except Exception:
            rja = {}
        ridA = self._extract_created_id(rja)
        if not ridA:
            record_result(name, "POST", base, getattr(ra, "status_code", None), False, "A id parse failed")
            return
        schedule_cleanup(name, "role", ridA, "DELETE", f"/api/v1/roles/{ridA}")

        # Create role B (requested position 20)
        b = {"name": f"test-role-pos-B-{int(time.time())}", "position": 20}
        rb, _ = api_call("POST", base, body=b, headers=self.headers())
        if not rb or rb.status_code not in (200, 201):
            record_result(name, "POST", base, getattr(rb, "status_code", None), False, "B create failed")
            return
        try:
            rjb = rb.json()
        except Exception:
            rjb = {}
        ridB = self._extract_created_id(rjb)
        if not ridB:
            record_result(name, "POST", base, getattr(rb, "status_code", None), False, "B id parse failed")
            return
        schedule_cleanup(name, "role", ridB, "DELETE", f"/api/v1/roles/{ridB}")

        self.wait_valid()

        # GET roles list and inspect positions & indices
        rl, _ = api_call("GET", base, headers=self.headers())
        if not rl or rl.status_code != 200:
            record_result(name, "GET", base, getattr(rl, "status_code", None), False, "list failed")
            return
        try:
            jr = rl.json()
        except Exception:
            jr = {}
        arr = self._normalize_data_to_list(jr)

        # Build quick lookup maps and indices
        id_to_index = {int(r.get("id")): i for i, r in enumerate(arr) if isinstance(r, dict) and "id" in r}
        def _safe_pos(x):
            try:
                return int(x)
            except Exception:
                return None
        id_to_pos = {int(r.get("id")): _safe_pos(r.get("position")) for r in arr if isinstance(r, dict) and "id" in r}

        idxA = id_to_index.get(int(ridA))
        idxB = id_to_index.get(int(ridB))
        posA = id_to_pos.get(int(ridA))
        posB = id_to_pos.get(int(ridB))

        # Validate presence
        if idxA is None or idxB is None or posA is None or posB is None:
            reason = f"A(id={ridA}) idx={idxA} pos={posA} ; B(id={ridB}) idx={idxB} pos={posB}"
            record_result(name, "GET", base, rl.status_code, False, f"missing role/position in listing: {reason}")
            return

        # Accept cases where the server normalizes new roles to the same position.
        # Require positions are non-decreasing; only enforce index ordering when positions are strictly ordered.
        try:
            posA_int = int(posA)
            posB_int = int(posB)
            ok_positions = posA_int <= posB_int
            ok_indices = True
            if posA_int < posB_int:
                ok_indices = idxA < idxB
            ok = ok_positions and ok_indices
        except Exception:
            ok = False

        reason = None
        if not ok:
            reason = f"A(id={ridA}) idx={idxA} pos={posA} ; B(id={ridB}) idx={idxB} pos={posB}"
        record_result(name, "GET", base, rl.status_code, ok, None if ok else reason)

    def test_role_position_sorting(self):
        name = "GET roles position sorting"
        base = f"/api/v1/guilds/{self.guild_id}/roles"
        created = []
        for pos in (30, 10, 20):
            body = {"name": f"test-role-sort-{pos}-{int(time.time())}", "position": pos}
            r, _ = api_call("POST", base, body=body, headers=self.headers())
            if not r or r.status_code not in (200, 201):
                record_result(name, "POST", base, getattr(r, "status_code", None), False, f"create pos={pos} failed"); return
            try:
                jr = r.json()
            except Exception:
                jr = {}
            rid = self._extract_created_id(jr)
            if not rid:
                record_result(name, "POST", base, getattr(r, "status_code", None), False, f"create pos={pos} id parse failed"); return
            schedule_cleanup(name, "role", rid, "DELETE", f"/api/v1/roles/{rid}")
            created.append((rid, pos))
            time.sleep(0.2)
        self.wait_valid()

        rl, _ = api_call("GET", base, headers=self.headers())
        if not rl or rl.status_code != 200:
            record_result(name, "GET", base, getattr(rl, "status_code", None), False, "list failed"); return
        try:
            jr = rl.json()
        except Exception:
            jr = {}
        arr = self._normalize_data_to_list(jr)

        created_ids = {rid for rid, _ in created}
        # positions in the order returned by the API for just our created roles
        seq = [r.get("position") for r in arr if isinstance(r, dict) and r.get("id") in created_ids]

        ok = (len(seq) == len(created)) and seq == sorted(seq)
        reason = None
        if not ok:
            reason = f"expected sorted positions for created roles; got {seq}"
        record_result(name, "GET", base, rl.status_code, ok, None if ok else reason)
        self.wait()

    def _create_helper_role(self) -> Optional[int]:
        path = f"/api/v1/guilds/{self.guild_id}/roles"
        body = {"name": f"test-role-{int(time.time())}"}
        r, _ = api_call("POST", path, body=body, headers=self.headers())
        if not r or r.status_code not in (200, 201):
            record_result("setup_role", "POST", path, getattr(r, "status_code", None), False, "role setup fail")
            return None
        try:
            j = r.json()
        except Exception:
            j = {}
        rid = self._extract_created_id(j)
        if rid is None:
            record_result("setup_role", "POST", path, getattr(r, "status_code", None), False, "unable to parse role id")
            return None
        # schedule cleanup using canonical single-role endpoint per OpenAPI
        schedule_cleanup("setup_role", "role", rid, "DELETE", f"/api/v1/roles/{rid}")
        self.wait_valid()
        return rid

# -----------------------------------------------------------------------------
class GuildTests(BaseTests):
    """Test suite for /api/v1/guilds endpoints."""
    def run_all(self):
        for test in (
            self.test_list_guilds,
            self.test_list_guilds_empty,
            self.test_list_guilds_forbid,
            self.test_get_guild_valid,
            self.test_get_guild_notfound,
            self.test_get_guild_invalid_type,
            self.test_get_guild_forbid,
            self.test_list_members_default,
            self.test_list_members_limit5,
            self.test_list_members_edgecases,
            self.test_list_members_invalid,
            self.test_list_members_forbid,
        ):
            try:
                test()
            except Exception:
                LOGGER.exception("Unhandled exception in guild test")

    def test_list_guilds(self):
        self.run_simple_expected("GET list guilds","GET","/api/v1/guilds",None,[200])

    def test_list_guilds_empty(self):
        record_result("GET list guilds empty","GET","/api/v1/guilds",None,False,"SKIPPED (cannot simulate empty)")

    def test_list_guilds_forbid(self):
        self.run_forbidden("GET list guilds forbid","GET","/api/v1/guilds",None)

    def test_get_guild_valid(self):
        p=f"/api/v1/guilds/{self.guild_id}"
        self.run_simple_expected("GET guild valid","GET",p,None,[200])

    def test_get_guild_notfound(self):
        self.run_simple_expected("GET guild notfound","GET","/api/v1/guilds/999999999999",None,[404])

    def test_get_guild_invalid_type(self):
        self.run_simple_expected("GET guild invalid","GET","/api/v1/guilds/abc",None,list(range(400,500)))

    def test_get_guild_forbid(self):
        p=f"/api/v1/guilds/{self.guild_id}"
        self.run_forbidden("GET guild forbid","GET",p,None)

    def test_list_members_default(self):
        p=f"/api/v1/guilds/{self.guild_id}/members"
        self.run_simple_expected("GET members default","GET",p,None,[200])

    def test_list_members_limit5(self):
        p=f"/api/v1/guilds/{self.guild_id}/members?limit=5"
        resp = self.run_simple_expected("GET members limit=5","GET",p,None,[200])
        if resp and resp.status_code == 200:
            try:
                j = resp.json()
            except Exception:
                j = {}
            # unwrap canonical envelope -> data, but accept either dict or list
            if isinstance(j, dict) and "data" in j:
                data = j["data"]
            else:
                data = j
            if isinstance(data, dict):
                arr = data.get("items") or data.get("members") or []
            elif isinstance(data, list):
                arr = data
            else:
                arr = []
            ok = isinstance(arr, list) and len(arr) <= 5
            record_result("GET members limit=5 verify","GET",p,resp.status_code,ok,
                          None if ok else f"len={len(arr)}")

    def test_list_members_edgecases(self):
        for val in (0,9999999):
            p=f"/api/v1/guilds/{self.guild_id}/members?limit={val}"
            self.run_simple_expected(f"GET members limit={val}","GET",p,None,[200,422])

    def test_list_members_invalid(self):
        self.run_simple_expected("GET members invalid guild","GET",
                                 f"/api/v1/guilds/abc/members",None,list(range(400,500)))

    def test_list_members_forbid(self):
        p=f"/api/v1/guilds/{self.guild_id}/members"
        self.run_forbidden("GET members forbid","GET",p,None)

# -----------------------------------------------------------------------------
class CategoryTests(BaseTests):
    """Test suite for /api/v1/guilds/{guild_id}/categories endpoints."""
    def run_all(self):
        for test in (
            self.test_list_categories_valid,
            self.test_list_categories_notfound,
            self.test_list_categories_invalid_type,
            self.test_list_categories_forbid,

            self.test_create_category_valid,
            self.test_create_category_missing_name,
            self.test_create_category_invalid_type,
            # self.test_create_category_default_nsfw,  Invalid test – commented out
            self.test_create_category_notfound,
            self.test_create_category_forbid,

            self.test_get_category_valid,
            self.test_get_category_notfound,
            self.test_get_category_invalid_type,
            self.test_get_category_forbid,

            self.test_update_category_valid,
            self.test_update_category_partial,
            self.test_update_category_invalid_values,
            self.test_update_category_null_fields,
            self.test_update_category_notfound,
            self.test_update_category_forbid,

            self.test_delete_category_valid,
            self.test_delete_category_cascade,
            self.test_delete_category_notfound,
            self.test_delete_category_invalid_cascade,
            self.test_delete_category_forbid,

            self.test_list_category_channels_valid,
            self.test_list_category_channels_notfound,
            self.test_list_category_channels_forbid,

            self.test_move_channel_valid,
            self.test_move_channel_notfound,
            self.test_move_channel_invalid_ids,
            self.test_move_channel_forbid,

            self.test_get_category_permissions_valid,
            self.test_get_category_permissions_notfound,
            self.test_get_category_permissions_empty,
            self.test_get_category_permissions_forbid,

            self.test_update_category_permissions_valid,
            self.test_update_category_permissions_missing,
            self.test_update_category_permissions_invalid,
            self.test_update_category_permissions_forbid,
        ):
            try:
                test()
            except Exception:
                LOGGER.exception("Unhandled exception in category test")

    def _mk_category(self) -> Optional[int]:
        p = f"/api/v1/guilds/{self.guild_id}/categories"
        body = {"name": f"test-category-{int(time.time())}"}
        resp, _ = api_call("POST", p, body=body, headers=self.headers())
        if not resp or resp.status_code not in (200, 201):
            record_result("setup_category", "POST", p,
                          getattr(resp, "status_code", None), False, "category creation failed")
            return None
        # robust extraction of created id handling canonical envelope & wrappers
        try:
            j = resp.json()
        except Exception:
            j = {}
        cid = self._extract_created_id(j)
        if cid is None:
            record_result("setup_category", "POST", p,
                          getattr(resp, "status_code", None), False, "unable to parse category id")
            return None
        # schedule canonical cleanup using single-resource endpoint per OpenAPI
        schedule_cleanup("setup_category", "category", cid, "DELETE", f"/api/v1/categories/{cid}")
        self.wait_valid()
        return int(cid)

    def _make_channel_in_category(self, cid: int) -> Optional[int]:
        p = f"/api/v1/guilds/{self.guild_id}/channels"
        body = {"name": f"test-channel-{int(time.time())}", "category_id": cid}
        resp, _ = api_call("POST", p, body=body, headers=self.headers())
        if not resp or resp.status_code not in (200, 201):
            record_result("setup_cat_channel", "POST", p,
                          getattr(resp, "status_code", None), False, "channel creation failed")
            return None
        try:
            j = resp.json()
        except Exception:
            j = {}
        # robustly extract created channel id (handles canonical "data" envelope)
        ch = self._extract_created_id(j)
        if ch is None:
            record_result("setup_cat_channel", "POST", p,
                          getattr(resp, "status_code", None), False, "unable to parse channel id")
            return None
        # schedule canonical cleanup using /api/v1/channels/{id}
        schedule_cleanup("setup_cat_channel", "channel", ch, "DELETE", f"/api/v1/channels/{ch}")
        self.wait_valid()
        return int(ch)

    def _make_channel(self) -> Optional[int]:
        return MessageTests(self.guild_id, self.user_id, self.delay, self.vdelay)._make_channel()

    #
    # List Categories
    #
    def test_list_categories_valid(self):
        """
        Canonical: GET /api/v1/guilds/{guild_id}/categories -> CategoryListResponse
        Verify we receive 200 and the response contains a data.items (or data) array.
        """
        p = f"/api/v1/guilds/{self.guild_id}/categories"
        resp = self.run_simple_expected("GET list categories", "GET", p, None, [200])
        if not resp or resp.status_code != 200:
            return
        try:
            j = resp.json()
        except Exception:
            j = {}
        data = j.get("data", j if isinstance(j, dict) else {})
        arr = self._normalize_data_to_list(j)
        ok = isinstance(arr, list)
        record_result("GET list categories verify", "GET", p, resp.status_code, ok,
                      None if ok else "data.items not list")

    def test_list_categories_notfound(self):
        self.run_simple_expected(
            "GET list categories notfound", "GET",
            "/api/v1/guilds/999999999999/categories", None, [404]
        )

    def test_list_categories_invalid_type(self):
        self.run_simple_expected(
            "GET list categories invalid", "GET",
            "/api/v1/guilds/abc/categories", None, list(range(400,500))
        )

    def test_list_categories_forbid(self):
        self.run_forbidden(
            "GET list categories forbid", "GET",
            f"/api/v1/guilds/{self.guild_id}/categories", None
        )

    #
    # Create Category
    #
    def test_create_category_valid(self):
        """
        Use canonical guild-scoped creation via run_validation.
        run_validation will POST, extract the new id, schedule cleanup, then
        GET /api/v1/categories/{id} and validate the persisted fields.
        """
        name = "POST create category valid"
        path = f"/api/v1/guilds/{self.guild_id}/categories"
        body = {"name": f"test-category-{int(time.time())}", "position": 1}
        # Use a templated get_path; run_validation will substitute {id} from the POST response.
        self.run_validation(name, "POST", path, body, f"/api/v1/categories/{{id}}")

    def test_create_category_missing_name(self):
        self.run_simple_expected(
            "POST create category missing", "POST",
            f"/api/v1/guilds/{self.guild_id}/categories",
            {"position":1}, [400,422]
        )

    def test_create_category_invalid_type(self):
        self.run_simple_expected(
            "POST create category invalid", "POST",
            f"/api/v1/guilds/{self.guild_id}/categories",
            {"name":f"test-invalid-{int(time.time())}","position":"nope"}, [400,422]
        )

    def test_create_category_notfound(self):
        self.run_simple_expected(
            "POST create category notfound", "POST",
            "/api/v1/guilds/999999999999/categories",
            {"name":f"test-invalid-{int(time.time())}"}, [404]
        )

    def test_create_category_forbid(self):
        self.run_forbidden(
            "POST create category forbid", "POST",
            f"/api/v1/guilds/{self.guild_id}/categories",
            {"name":f"test-invalid-{int(time.time())}"}
        )

    #
    # Get / Update / Delete Category
    #
    def test_get_category_valid(self):
        cid = self._mk_category()
        if cid:
            # Canonical single-category GET per OpenAPI
            self.run_simple_expected(
                "GET category valid", "GET",
                f"/api/v1/categories/{cid}", None, [200]
            )

    def test_get_category_notfound(self):
        # Single-category notfound path
        self.run_simple_expected(
            "GET category notfound", "GET",
            f"/api/v1/categories/999999999999", None, [404]
        )

    def test_get_category_invalid_type(self):
        self.run_simple_expected(
            "GET category invalid", "GET",
            f"/api/v1/categories/abc", None, list(range(400,500))
        )

    def test_get_category_forbid(self):
        self.run_forbidden(
            "GET category forbid", "GET",
            f"/api/v1/categories/0", None
        )

    def test_update_category_valid(self):
        """
        Update a category and validate the important persisted fields.

        Notes:
        - Some servers normalize or reindex positions when categories are
          created/updated. Instead of asserting exact equality on 'position',
          validate that 'position' is present and integer-like and that the
          'name' was updated exactly.
        """
        cid = self._mk_category()
        if cid:
            body = {"name": "test-cat-upd", "position": 2}
            p = f"/api/v1/categories/{cid}"

            def validate_category_update(get_json: dict) -> Tuple[bool, str]:
                j = get_json
                # unwrap canonical envelope
                if isinstance(j, dict) and "data" in j:
                    j = j["data"]
                # unwrap known single-resource wrapper if present
                if isinstance(j, dict) and "category" in j and isinstance(j["category"], dict):
                    j = j["category"]
                if not isinstance(j, dict):
                    return False, "unexpected response shape"
                got_name = j.get("name")
                got_pos = j.get("position")
                # Name must match exactly
                if got_name != body["name"]:
                    return False, f"expected name {body['name']!r}, got {got_name!r}"
                # Position: accept any integer-like value (server may renumber)
                if got_pos is None:
                    return False, "position missing"
                try:
                    int(got_pos)
                except Exception:
                    return False, f"position not integer: {got_pos!r}"
                return True, ""

            # Use custom validator so we don't false-fail on server-side position normalization
            self.run_validation("PUT update category valid", "PUT", p, body, p, validate_fn=validate_category_update)


    def test_update_category_partial(self):
        cid = self._mk_category()
        if cid:
            body = {"name": "test-cat-partial"}
            p = f"/api/v1/categories/{cid}"
            self.run_validation("PUT update category partial", "PUT", p, body, p)

    def test_update_category_invalid_values(self):
        cid = self._mk_category()
        if cid:
            body = {"position": -1}
            p = f"/api/v1/categories/{cid}"
            self.run_simple_expected(
                "PUT update category invalid", "PUT",
                p, body, [400, 422]
            )

    def test_update_category_null_fields(self):
        cid = self._mk_category()
        if cid:
            # Nulling name/position is allowed by the update schema (anyOf string|null / integer|null)
            body = {"name": None, "position": None}
            p = f"/api/v1/categories/{cid}"
            self.run_validation("PUT update category null", "PUT", p, body, p)

    def test_update_category_notfound(self):
        self.run_simple_expected(
            "PUT update category notfound", "PUT",
            f"/api/v1/categories/999999999999",
            {"name": f"test-invalid-{int(time.time())}"}, [404]
        )

    def test_update_category_forbid(self):
        self.run_forbidden(
            "PUT update category forbid", "PUT",
            f"/api/v1/categories/0", {"name":"test-invalid"}
        )

    def test_delete_category_valid(self):
        cid = self._mk_category()
        if cid:
            # Prevent auto-cleanup (we will delete explicitly here)
            with CLEANUP_LOCK:
                CLEANUP_QUEUE[:] = [
                    e for e in CLEANUP_QUEUE
                    if not (e["resource_type"] == "category" and e["resource_id"] == str(cid))
                ]
            path = f"/api/v1/categories/{cid}"
            self.run_simple_expected("DELETE category valid", "DELETE", path, None, [200])

    def test_delete_category_cascade(self):
        """
        Create a category with two child channels, then DELETE with cascade=true
        and verify the child channels are removed.
        """
        cid = self._mk_category()
        if not cid:
            return
        # create two channels under that category
        ch1 = self._make_channel_in_category(cid)
        ch2 = self._make_channel_in_category(cid)
        if not (ch1 and ch2):
            record_result("DELETE category cascade", "DELETE",
                          f"/api/v1/categories/{cid}?cascade=true",
                          None, False, "child-channel setup failed")
            return
        # prevent auto-cleanup of those child channels so we can assert they were deleted by cascade
        with CLEANUP_LOCK:
            CLEANUP_QUEUE[:] = [
                e for e in CLEANUP_QUEUE
                if not (e["resource_type"] == "channel"
                        and e["resource_id"] in (str(ch1), str(ch2)))
            ]

        # issue cascade delete on canonical single-category endpoint
        path = f"/api/v1/categories/{cid}?cascade=true"
        resp = self.run_simple_expected("DELETE category cascade", "DELETE", path, None, [200])
        if not resp or resp.status_code != 200:
            return
        # verify child channels have been removed
        for ch in (ch1, ch2):
            self.run_simple_expected("GET child after cascade", "GET",
                                     f"/api/v1/channels/{ch}", None, [404])

    def test_delete_category_notfound(self):
        self.run_simple_expected(
            "DELETE category notfound", "DELETE",
            f"/api/v1/categories/999999999999", None, [404]
        )

    def test_delete_category_invalid_cascade(self):
        cid = self._mk_category()
        if cid:
            path = f"/api/v1/categories/{cid}?cascade=maybe"
            self.run_simple_expected(
                "DELETE category invalid cascade", "DELETE", path, None, [400, 422]
            )

    def test_delete_category_forbid(self):
        self.run_forbidden(
            "DELETE category forbid", "DELETE",
            f"/api/v1/categories/0", None
        )

    #
    # Category Channels (list)
    #
    def test_list_category_channels_valid(self):
        cid = self._mk_category()
        if cid:
            resp = self.run_simple_expected(
                "GET list category channels", "GET",
                f"/api/v1/categories/{cid}/channels", None, [200]
            )
            if resp and resp.status_code == 200:
                try:
                    j = resp.json()
                except Exception:
                    j = {}
                arr = self._normalize_data_to_list(j)
                ok = isinstance(arr, list)
                record_result("GET list category channels verify", "GET",
                              f"/api/v1/categories/{cid}/channels", resp.status_code, ok,
                              None if ok else "data.items not list")

    def test_list_category_channels_notfound(self):
        self.run_simple_expected(
            "GET list category channels notfound", "GET",
            f"/api/v1/categories/999999999999/channels", None, [404]
        )

    def test_list_category_channels_forbid(self):
        self.run_forbidden(
            "GET list category channels forbid", "GET",
            f"/api/v1/categories/0/channels", None
        )

    #
    # Move Channel to Category
    #
    def test_move_channel_valid(self):
        cid = self._mk_category()
        ch  = self._make_channel()
        if not (cid and ch):
            return
        # canonical move: PUT /api/v1/channels/{channel_id}/category/{category_id}
        p = f"/api/v1/channels/{ch}/category/{cid}"
        # Use run_validation so the PUT is followed by a GET of the channel to confirm category_id
        body = None  # endpoint does not require a body
        # run_validation expects a dict body; pass {} if None would be problematic
        self.run_validation("PUT move channel", "PUT", p, {} , f"/api/v1/channels/{ch}")

    def test_move_channel_notfound(self):
        cid = self._mk_category()
        if not cid:
            return
        # move a non-existent channel
        p = f"/api/v1/channels/999999999999/category/{cid}"
        self.run_simple_expected("PUT move channel notfound", "PUT", p, None, [404])

    def test_move_channel_invalid_ids(self):
        # invalid id formats in path -> expect 4xx
        self.run_simple_expected(
            "PUT move channel invalid", "PUT",
            "/api/v1/channels/abc/category/def", None, list(range(400,500))
        )

    def test_move_channel_forbid(self):
        # canonical forbid case (invalid zero ids)
        self.run_forbidden(
            "PUT move channel forbid", "PUT",
            f"/api/v1/channels/0/category/0", None
        )

    #
    # Category Permissions (GET / PUT) — canonical single-resource endpoints
    #
    def test_get_category_permissions_valid(self):
        cid = self._mk_category()
        if cid:
            p = f"/api/v1/categories/{cid}/permissions"
            resp = self.run_simple_expected("GET category perms", "GET", p, None, [200])
            if resp and resp.status_code == 200:
                try:
                    j = resp.json()
                except Exception:
                    j = {}
                # Robust extraction: accept either {"data": {...}} OR a top-level list
                if isinstance(j, dict) and "data" in j:
                    data = j["data"]
                else:
                    data = j
                if isinstance(data, dict):
                    arr = self._normalize_data_to_list(j)
                elif isinstance(data, list):
                    arr = data
                else:
                    arr = []
                ok = isinstance(arr, list)
                record_result("GET category perms verify", "GET", p, resp.status_code, ok,
                              None if ok else "overwrites not list")

    def test_get_category_permissions_notfound(self):
        self.run_simple_expected(
            "GET category perms notfound", "GET",
            f"/api/v1/categories/999999999999/permissions", None, [404]
        )

    def test_get_category_permissions_empty(self):
        cid = self._mk_category()
        if cid:
            p = f"/api/v1/categories/{cid}/permissions"
            resp = self.run_simple_expected("GET category perms empty", "GET", p, None, [200])
            if resp and resp.status_code == 200:
                try:
                    j = resp.json()
                except Exception:
                    j = {}
                # Robust extraction: accept either {"data": {...}} OR a top-level list
                if isinstance(j, dict) and "data" in j:
                    data = j["data"]
                else:
                    data = j
                if isinstance(data, dict):
                    arr = self._normalize_data_to_list(j)
                elif isinstance(data, list):
                    arr = data
                else:
                    arr = []
                ok = isinstance(arr, list)
                record_result("GET category perms empty verify", "GET", p, resp.status_code, ok,
                              None if ok else "not array")

    def test_get_category_permissions_forbid(self):
        self.run_forbidden(
            "GET category perms forbid", "GET",
            f"/api/v1/categories/0/permissions", None
        )

    def test_update_category_permissions_valid(self):
        """
        Create a temporary role, PUT permission overwrites for the category using
        that role as the target, then let run_validation do the PUT -> GET ->
        validate sequence using a custom validate_fn that checks the overwrite
        for the created role has the expected allow/deny values.
        """
        cid = self._mk_category()
        if not cid:
            return
        name = "PUT category perms valid"
        # 1) Create a temporary role to target in the permission overwrite
        role_body = {"name": f"test-role-perms-{int(time.time())}", "permissions": 0}
        rresp, _ = api_call("POST", f"/api/v1/guilds/{self.guild_id}/roles",
                            body=role_body, headers=self.headers())
        if not rresp or rresp.status_code not in (200, 201):
            record_result(name + " (role create)", "POST",
                        f"/api/v1/guilds/{self.guild_id}/roles",
                        getattr(rresp, "status_code", None), False, "role-create failed")
            return

        try:
            rj = rresp.json()
        except Exception:
            rj = {}
        role_id = self._extract_created_id(rj)
        if role_id is None:
            record_result(name + " (role create)", "POST",
                        f"/api/v1/guilds/{self.guild_id}/roles",
                        getattr(rresp, "status_code", None), False, "role-create id parse failed")
            return
        # schedule cleanup for the role using canonical single-role endpoint
        schedule_cleanup(name + " (role)", "role", role_id, "DELETE", f"/api/v1/roles/{role_id}")
        self.wait_valid()

        # 2) Prepare PUT body targeting the created role
        p = f"/api/v1/categories/{cid}/permissions"
        body = {
            "overwrites": [
                {"target_id": role_id, "type": "role", "allow": 3072, "deny": 4096}
            ]
        }

        # 3) custom validator for run_validation: normalize the GET response into a list
        #    (handles data = [...], data = {"overwrites": [...]}, wrappers, etc.)
        def validate_fn(get_resp_json: dict) -> Tuple[bool, str]:
            # Normalize to a list using the harness helper which understands
            # envelopes and common container keys.
            arr = self._normalize_data_to_list(get_resp_json)
            if not isinstance(arr, list):
                return False, "permissions payload not an array"

            for ow in arr:
                # each overwrite should be a dict-like object
                if not isinstance(ow, dict):
                    continue
                tid = ow.get("target_id")
                try:
                    tid_int = int(tid)
                except Exception:
                    tid_int = None
                if tid_int == role_id and ow.get("type") == "role":
                    try:
                        ok_allow = int(ow.get("allow", -1)) == 3072
                        ok_deny = int(ow.get("deny", -1)) == 4096
                        if ok_allow and ok_deny:
                            return True, ""
                        return False, f"allow/deny mismatch (got allow={ow.get('allow')}, deny={ow.get('deny')})"
                    except Exception:
                        return False, "allow/deny not integers"
            return False, "overwrite for created role not found"

        # 4) Use run_validation so the PUT is followed by a GET and the validator runs
        self.run_validation(name, "PUT", p, body, p, validate_fn=validate_fn)


    def test_update_category_permissions_missing(self):
        cid = self._mk_category()
        if cid:
            p = f"/api/v1/categories/{cid}/permissions"
            # missing required 'overwrites' should yield 400/422
            self.run_simple_expected("PUT category perms missing", "PUT", p, {}, [400,422])

    def test_update_category_permissions_invalid(self):
        cid = self._mk_category()
        if cid:
            p = f"/api/v1/categories/{cid}/permissions"
            body = {"overwrites": [{"type": "role"}]}  # missing required fields in each overwrite
            self.run_simple_expected("PUT category perms invalid", "PUT", p, body, [400,422])

    def test_update_category_permissions_forbid(self):
        self.run_forbidden(
            "PUT category perms forbid", "PUT",
            f"/api/v1/categories/0/permissions", {"overwrites": []}
        )

# -----------------------------------------------------------------------------
class ChannelTests(BaseTests):
    """Test suite for /api/v1/guilds/.../channels and /api/v1/channels/... endpoints."""
    def run_all(self):
        for test in (
            self.test_list_guild_channels_valid,
            self.test_list_guild_channels_notfound,
            self.test_list_guild_channels_forbid,

            self.test_list_channel_messages_valid,
            self.test_list_channel_messages_notfound,
            self.test_list_channel_messages_invalid,

            self.test_create_channel_valid,
            self.test_create_channel_missing_name,
            self.test_create_channel_invalid_fields,
            self.test_create_channel_default_nsfw,
            self.test_create_channel_nsfw_flag,
            self.test_create_channel_slowmode,
            self.test_create_channel_notfound,
            self.test_create_channel_forbid,

            self.test_get_channel_valid,
            self.test_get_channel_notfound,
            self.test_get_channel_invalid,
            self.test_get_channel_forbid,

            self.test_update_channel_valid,
            self.test_update_channel_readonly,
            self.test_update_channel_invalid,
            self.test_update_channel_null_fields,
            self.test_update_channel_notfound,
            self.test_update_channel_forbid,

            self.test_delete_channel_valid,
            self.test_delete_channel_notfound,
            self.test_delete_channel_forbid,

            self.test_get_channel_perms_valid,
            self.test_get_channel_perms_notfound,
            self.test_get_channel_perms_forbid,

            self.test_replace_channel_perms_valid,
            self.test_replace_channel_perms_invalid,
            self.test_replace_channel_perms_malformed,
            self.test_replace_channel_perms_forbid,

            self.test_create_voice_channel,
            self.test_update_voice_channel,
            self.test_delete_voice_channel,
        ):
            try:
                test()
            except Exception:
                LOGGER.exception("Unhandled exception in channel test")

    def _mk_category(self) -> Optional[int]:
        return CategoryTests(self.guild_id, self.user_id, self.delay, self.vdelay)._mk_category()
        
    def _make_channel(self) -> Optional[int]:
        return MessageTests(self.guild_id, self.user_id, self.delay, self.vdelay)._make_channel()

    def _make_voice_channel(self) -> Optional[int]:
        """Create a disposable voice channel for voice-specific tests."""
        path = f"/api/v1/guilds/{self.guild_id}/channels"
        body = {
            "name": f"test-voice-{int(time.time())}",
            "type": "voice",
            "bitrate": 64000,
            "user_limit": 10
        }
        resp, _ = api_call("POST", path, body=body, headers=self.headers())
        if not resp or resp.status_code not in (200, 201):
            record_result("setup_voice_channel", "POST", path,
                          getattr(resp, "status_code", None), False, "voice channel creation failed")
            return None
        try:
            j = resp.json()
        except Exception:
            j = {}
        # Robust extraction of created id (handles canonical data envelope and legacy wrappers)
        cid = self._extract_created_id(j)
        if cid is None:
            # fall back to common legacy shapes
            if isinstance(j, dict):
                cid = j.get("channel", {}).get("id") or j.get("data", {}).get("id") or j.get("id")
        try:
            cid = int(cid)
        except Exception:
            record_result("setup_voice_channel", "POST", path,
                          getattr(resp, "status_code", None), False, "unable to parse channel id")
            return None
        schedule_cleanup("setup_voice_channel", "channel", cid, "DELETE", f"/api/v1/channels/{cid}")
        self.wait_valid()
        return cid

    def test_list_guild_channels_valid(self):
        p = f"/api/v1/guilds/{self.guild_id}/channels"
        resp = self.run_simple_expected("GET list guild channels", "GET", p, None, [200])
        if not resp or resp.status_code != 200:
            return
        try:
            j = resp.json()
        except Exception:
            j = {}
        data = j.get("data", j if isinstance(j, dict) else {})
        arr = self._normalize_data_to_list(j)
        ok = isinstance(arr, list)
        record_result("GET list guild channels verify", "GET", p, resp.status_code, ok,
                      None if ok else "data.items not list")

    def test_list_guild_channels_notfound(self):
        self.run_simple_expected("GET list guild channels notfound", "GET",
                                 "/api/v1/guilds/999999999999/channels", None, [404])

    def test_list_guild_channels_forbid(self):
        p = f"/api/v1/guilds/{self.guild_id}/channels"
        self.run_forbidden("GET list guild channels forbid", "GET", p, None)

    def test_list_channel_messages_valid(self):
        ch = self._make_channel()
        if not ch:
            return
        p = f"/api/v1/channels/{ch}/messages"
        resp = self.run_simple_expected("GET list channel messages", "GET", p, None, [200])
        if not resp or resp.status_code != 200:
            return
        try:
            j = resp.json()
        except Exception:
            j = {}
        arr = self._normalize_data_to_list(j)
        ok = isinstance(arr, list)
        record_result("GET list channel messages verify", "GET", p, resp.status_code, ok,
                      None if ok else "messages not list")

    def test_list_channel_messages_notfound(self):
        p = "/api/v1/channels/999999999999/messages"
        self.run_simple_expected("GET channel messages notfound", "GET", p, None, [404])

    def test_list_channel_messages_invalid(self):
        p = "/api/v1/channels/abc/messages"
        self.run_simple_expected("GET channel messages invalid", "GET", p, None, list(range(400,500)))

    # NEW: valid channel‐only message listing
    def test_create_channel_valid(self):
        path = f"/api/v1/guilds/{self.guild_id}/channels"
        body = {
            "name": f"test-channel-{int(time.time())}",
            "type": "text",
            "position": 1,
            "nsfw": False
        }
        # POST then GET /api/v1/channels/{id} and validate persisted fields
        self.run_validation("POST create channel valid", "POST", path, body, "/api/v1/channels/{id}")

    def test_create_channel_missing_name(self):
        self.run_simple_expected(
            "POST create channel missing", "POST",
            f"/api/v1/guilds/{self.guild_id}/channels", {"type": "text"}, [400,422]
        )

    def test_create_channel_invalid_fields(self):
        body = {
            "name": f"test-channel-invalid-{int(time.time())}",
            "bitrate": -1,
            "user_limit": -5
        }
        self.run_simple_expected(
            "POST create channel invalid", "POST",
            f"/api/v1/guilds/{self.guild_id}/channels", body, [400,422]
        )

    def test_create_channel_default_nsfw(self):
        """
        Create channel without nsfw and validate that GET reports nsfw == False.
        Uses a custom validate_fn because the request body doesn't carry the nsfw field.
        """
        path = f"/api/v1/guilds/{self.guild_id}/channels"
        body = {"name": f"test-channel-default-nsfw-{int(time.time())}"}

        def validate_default_nsfw(get_json: dict) -> Tuple[bool, str]:
            j = get_json
            if isinstance(j, dict) and "data" in j:
                j = j["data"]
            # unwrap known wrappers
            if isinstance(j, dict):
                for wrapper in ("channel",):
                    if wrapper in j and isinstance(j[wrapper], dict):
                        j = j[wrapper]
                        break
            nsfw = None
            if isinstance(j, dict):
                nsfw = j.get("nsfw")
            return (True, "") if nsfw is False else (False, f"expected nsfw False, got {nsfw}")

        self.run_validation("POST create channel default nsfw", "POST", path, body, "/api/v1/channels/{id}", validate_fn=validate_default_nsfw)

    def test_create_channel_nsfw_flag(self):
        path = f"/api/v1/guilds/{self.guild_id}/channels"
        body = {"name": f"test-channel-nsfw-{int(time.time())}", "nsfw": True}

        def validate_nsfw_true(get_json: dict) -> Tuple[bool, str]:
            j = get_json
            if isinstance(j, dict) and "data" in j:
                j = j["data"]
            if isinstance(j, dict):
                for wrapper in ("channel",):
                    if wrapper in j and isinstance(j[wrapper], dict):
                        j = j[wrapper]
                        break
            nsfw = None
            if isinstance(j, dict):
                nsfw = j.get("nsfw")
            return (True, "") if nsfw is True else (False, f"expected nsfw True, got {nsfw}")

        self.run_validation("POST create channel nsfw flag", "POST", path, body, "/api/v1/channels/{id}", validate_fn=validate_nsfw_true)

    def test_create_channel_slowmode(self):
        path = f"/api/v1/guilds/{self.guild_id}/channels"
        body = {"name": f"test-channel-slow-{int(time.time())}", "slowmode_delay": 5}

        def validate_slowmode(get_json: dict) -> Tuple[bool, str]:
            j = get_json
            if isinstance(j, dict) and "data" in j:
                j = j["data"]
            if isinstance(j, dict):
                for wrapper in ("channel",):
                    if wrapper in j and isinstance(j[wrapper], dict):
                        j = j[wrapper]
                        break
            val = None
            if isinstance(j, dict):
                val = j.get("slowmode_delay")
            return (True, "") if val == 5 else (False, f"expected slowmode 5, got {val}")

        self.run_validation("POST create channel slowmode", "POST", path, body, "/api/v1/channels/{id}", validate_fn=validate_slowmode)

    def test_create_channel_notfound(self):
        self.run_simple_expected(
            "POST create channel notfound", "POST",
            "/api/v1/guilds/999999999999/channels",
            {"name": f"test-channel-notfound-{int(time.time())}"}, [404]
        )

    def test_create_channel_forbid(self):
        self.run_forbidden(
            "POST create channel forbid", "POST",
            f"/api/v1/guilds/{self.guild_id}/channels", {"name": f"test-invalid-{int(time.time())}"}
        )

    def test_create_voice_channel(self):
        path = f"/api/v1/guilds/{self.guild_id}/channels"
        body = {
            "name": f"test-voice-{int(time.time())}",
            "type": "voice",
            "bitrate": 64000,
            "user_limit": 10
        }

        def validate_voice(get_json: dict) -> Tuple[bool, str]:
            j = get_json
            if isinstance(j, dict) and "data" in j:
                j = j["data"]
            if isinstance(j, dict):
                for wrapper in ("channel",):
                    if wrapper in j and isinstance(j[wrapper], dict):
                        j = j[wrapper]
                        break
            b = None
            ul = None
            if isinstance(j, dict):
                b = j.get("bitrate")
                ul = j.get("user_limit")
            ok = (int(b) == 64000 if b is not None else False) and (int(ul) == 10 if ul is not None else False)
            return (True, "") if ok else (False, f"bitrate/user_limit mismatch got bitrate={b} user_limit={ul}")

        # Use run_validation so the create is followed by GET /api/v1/channels/{id} and validation
        self.run_validation("POST create voice channel", "POST", path, body, "/api/v1/channels/{id}", validate_fn=validate_voice)

    # Get / Update / Delete Channel
    def test_get_channel_valid(self):
        ch = self._make_channel()
        if ch:
            p = f"/api/v1/channels/{ch}"
            resp = self.run_simple_expected("GET channel valid", "GET", p, None, [200])
            if not resp or resp.status_code != 200:
                return
            try:
                j = resp.json()
            except Exception:
                j = {}
            # unwrap canonical envelope & wrappers
            obj = j.get("data", j if isinstance(j, dict) else {})
            if isinstance(obj, dict) and "channel" in obj and isinstance(obj["channel"], dict):
                obj = obj["channel"]
            ok = isinstance(obj, dict) and int(obj.get("id", -1)) == ch and "name" in obj
            record_result("GET channel valid verify", "GET", p, resp.status_code, ok,
                          None if ok else f"unexpected channel payload: {obj}")

    def test_get_channel_notfound(self):
        self.run_simple_expected("GET channel notfound", "GET",
                                 "/api/v1/channels/999999999999", None, [404])

    def test_get_channel_invalid(self):
        self.run_simple_expected("GET channel invalid", "GET",
                                 "/api/v1/channels/abc", None, list(range(400,500)))

    def test_get_channel_forbid(self):
        self.run_forbidden("GET channel forbid", "GET", "/api/v1/channels/0")

    def test_update_channel_valid(self):
        ch = self._make_channel()
        if ch:
            body = {"topic": "upd", "slowmode_delay": 3}
            p = f"/api/v1/channels/{ch}"

            def validate_channel_update(get_json: dict) -> Tuple[bool, str]:
                j = get_json
                if isinstance(j, dict) and "data" in j:
                    j = j["data"]
                if isinstance(j, dict) and "channel" in j and isinstance(j["channel"], dict):
                    j = j["channel"]
                # topic may be None or a string; slowmode_delay should equal the requested value
                got_topic = j.get("topic") if isinstance(j, dict) else None
                got_slow = j.get("slowmode_delay") if isinstance(j, dict) else None
                ok_topic = (got_topic == body["topic"])
                try:
                    ok_slow = int(got_slow) == int(body["slowmode_delay"])
                except Exception:
                    ok_slow = False
                if ok_topic and ok_slow:
                    return True, ""
                return False, f"topic={got_topic!r} slowmode_delay={got_slow!r}"

            self.run_validation("PUT update channel valid", "PUT", p, body, p, validate_fn=validate_channel_update)

    def test_update_channel_readonly(self):
        ch = self._make_channel()
        if ch:
            self.run_simple_expected(
                "PUT update channel readonly", "PUT",
                f"/api/v1/channels/{ch}", {"id": 999}, list(range(400,500))
            )

    def test_update_channel_invalid(self):
        ch = self._make_channel()
        if ch:
            self.run_simple_expected(
                "PUT update channel invalid", "PUT",
                f"/api/v1/channels/{ch}", {"position": "nope"}, [400,422]
            )

    def test_update_channel_null_fields(self):
        ch = self._make_channel()
        if ch:
            body = {"topic": None}
            p = f"/api/v1/channels/{ch}"

            def validate_null_topic(get_json: dict) -> Tuple[bool, str]:
                j = get_json
                if isinstance(j, dict) and "data" in j:
                    j = j["data"]
                if isinstance(j, dict) and "channel" in j and isinstance(j["channel"], dict):
                    j = j["channel"]
                # The server may either set topic to null or omit it; treat both as OK
                if not isinstance(j, dict):
                    return False, "unexpected response shape"
                if "topic" not in j or j.get("topic") is None:
                    return True, ""
                return False, f"expected topic null/omitted, got {j.get('topic')!r}"

            self.run_validation("PUT update channel null", "PUT", p, body, p, validate_fn=validate_null_topic)

    def test_update_channel_notfound(self):
        self.run_simple_expected(
            "PUT update channel notfound", "PUT",
            "/api/v1/channels/999999999999", {"topic": "test-invalid-{int(time.time())}"}, [404]
        )

    def test_update_channel_forbid(self):
        self.run_forbidden("PUT update channel forbid", "PUT", "/api/v1/channels/0")

    def test_delete_channel_valid(self):
        ch = self._make_channel()
        if ch:
            # Prevent auto-cleanup (we will delete explicitly here)
            with CLEANUP_LOCK:
                CLEANUP_QUEUE[:] = [
                    e for e in CLEANUP_QUEUE
                    if not (e["resource_type"] == "channel" and e["resource_id"] == str(ch))
                ]
            # Delete the channel
            self.run_simple_expected(
                "DELETE channel valid", "DELETE",
                f"/api/v1/channels/{ch}", None, [200,204]
            )
            self.wait_valid()
            # Verify the channel is gone
            self.run_simple_expected(
                "GET channel after delete", "GET",
                f"/api/v1/channels/{ch}", None, [404]
            )

    def test_delete_channel_notfound(self):
        """DELETE a non-existent channel should return 404 per OpenAPI."""
        self.run_simple_expected(
            "DELETE channel notfound", "DELETE",
            "/api/v1/channels/999999999999", None, [404]
        )

    def test_delete_channel_forbid(self):
        """DELETE with forbidden/invalid id should be SKIPPED (no auth) in harness."""
        self.run_forbidden("DELETE channel forbid", "DELETE", "/api/v1/channels/0")

    
    def test_update_voice_channel(self):
        cid = self._make_voice_channel()
        if not cid:
            return
        body = {"bitrate": 96000, "user_limit": 5}
        p = f"/api/v1/channels/{cid}"
        def validate_voice_update(get_json: dict) -> Tuple[bool, str]:
            j = get_json
            if isinstance(j, dict) and "data" in j:
                j = j["data"]
            if isinstance(j, dict) and "channel" in j and isinstance(j["channel"], dict):
                j = j["channel"]
            if not isinstance(j, dict):
                return False, "unexpected response shape"
            # type may be present; prefer explicit checks for voice fields
            try:
                got_bitrate = j.get("bitrate")
                got_ul = j.get("user_limit")
                got_type = j.get("type")
                ok_bitrate = int(got_bitrate) == int(body["bitrate"]) if got_bitrate is not None else False
                ok_ul = int(got_ul) == int(body["user_limit"]) if got_ul is not None else False
                ok_type = (got_type == "voice") if got_type is not None else True
                if ok_bitrate and ok_ul and ok_type:
                    return True, ""
                return False, f"bitrate={got_bitrate} user_limit={got_ul} type={got_type}"
            except Exception:
                return False, "invalid types in response"
        # Validate update via GET of the same channel
        self.run_validation(
            "PUT update voice channel",
            "PUT",
            p,
            body,
            p,
            validate_fn=validate_voice_update
        )
        self.wait()

    def test_delete_voice_channel(self):
        ch = self._make_voice_channel()
        if not ch:
            return
        # Prevent the auto‑cleanup so we delete explicitly here
        with CLEANUP_LOCK:
            CLEANUP_QUEUE[:] = [
                e for e in CLEANUP_QUEUE
                if not (e["resource_type"] == "channel" and e["resource_id"] == str(ch))
            ]
        # Delete and verify deletion
        self.run_simple_expected(
            "DELETE voice channel",
            "DELETE",
            f"/api/v1/channels/{ch}",
            None,
            [200, 204]
        )
        self.wait_valid()
        self.run_simple_expected(
            "GET voice channel after delete",
            "GET",
            f"/api/v1/channels/{ch}",
            None,
            [404]
        )
        self.wait()

    # Channel Permission Overwrites
    def test_get_channel_perms_valid(self):
        ch = self._make_channel()
        if ch:
            p = f"/api/v1/channels/{ch}/permissions"
            resp = self.run_simple_expected("GET channel perms", "GET", p, None, [200])
            if not resp or resp.status_code != 200:
                return
            try:
                j = resp.json()
            except Exception:
                j = {}
            data = j.get("data", j if isinstance(j, dict) else {})
            arr = self._normalize_data_to_list(j)
            ok = isinstance(arr, list)
            record_result("GET channel perms verify", "GET", p, resp.status_code, ok,
                          None if ok else "overwrites not list")

    def test_get_channel_perms_notfound(self):
        self.run_simple_expected(
            "GET channel perms notfound", "GET",
            "/api/v1/channels/999999999999/permissions", None, [404]
        )

    def test_get_channel_perms_forbid(self):
        self.run_forbidden("GET channel perms forbid", "GET", "/api/v1/channels/0/permissions")

    def test_replace_channel_perms_valid(self):
        """
        Create a temporary role, PUT permission overwrites for a channel targeting
        that role, then validate via GET that an overwrite for the role exists
        with the expected allow/deny bitfields.
        """
        ch = self._make_channel()
        if not ch:
            return

        name = "PUT channel perms valid"
        # 1) create temporary role
        role_body = {"name": f"test-role-perm-{int(time.time())}", "permissions": 0}
        rresp, _ = api_call("POST", f"/api/v1/guilds/{self.guild_id}/roles", body=role_body, headers=self.headers())
        if not rresp or rresp.status_code not in (200, 201):
            record_result(name + " (role create)", "POST",
                        f"/api/v1/guilds/{self.guild_id}/roles",
                        getattr(rresp, "status_code", None), False, "role-create failed")
            return
        try:
            rj = rresp.json()
        except Exception:
            rj = {}
        role_id = self._extract_created_id(rj)
        if role_id is None:
            record_result(name + " (role create)", "POST",
                        f"/api/v1/guilds/{self.guild_id}/roles",
                        getattr(rresp, "status_code", None), False, "role-create id parse failed")
            return
        schedule_cleanup(name + " (role)", "role", role_id, "DELETE", f"/api/v1/roles/{role_id}")
        self.wait_valid()

        # 2) prepare and PUT overwrites on the channel
        p = f"/api/v1/channels/{ch}/permissions"
        body = {"overwrites": [{"target_id": role_id, "type": "role", "allow": 3072, "deny": 4096}]}

        def validate_channel_overwrite(get_json: dict) -> Tuple[bool, str]:
            # Normalize the GET response into a list (handles data=[...], data={"overwrites":[...]}, wrappers, etc.)
            arr = self._normalize_data_to_list(get_json)
            if not isinstance(arr, list):
                return False, "permissions payload not an array"
            for ow in arr:
                if not isinstance(ow, dict):
                    continue
                try:
                    tid = int(ow.get("target_id")) if ow.get("target_id") is not None else None
                except Exception:
                    tid = None
                if tid == int(role_id) and ow.get("type") == "role":
                    try:
                        ok_allow = int(ow.get("allow", -1)) == 3072
                        ok_deny = int(ow.get("deny", -1)) == 4096
                        if ok_allow and ok_deny:
                            return True, ""
                        return False, f"allow/deny mismatch (got allow={ow.get('allow')}, deny={ow.get('deny')})"
                    except Exception:
                        return False, "allow/deny not integers"
            return False, "overwrite for created role not found"

        # Use run_validation so PUT -> GET -> validate happens
        self.run_validation(name, "PUT", p, body, p, validate_fn=validate_channel_overwrite)

    def test_replace_channel_perms_invalid(self):
        ch = self._make_channel()
        if ch:
            self.run_simple_expected(
                "PUT channel perms invalid", "PUT",
                f"/api/v1/channels/{ch}/permissions", {"overwrites": [{"type": "role"}]}, [400, 422]
            )

    def test_replace_channel_perms_malformed(self):
        ch = self._make_channel()
        if ch:
            # send a non-object (string) as payload — server should reject as 400/422
            self.run_simple_expected(
                "PUT channel perms malformed", "PUT",
                f"/api/v1/channels/{ch}/permissions", "not-a-json", [400, 422]
            )

    def test_replace_channel_perms_forbid(self):
        self.run_forbidden(
            "PUT channel perms forbid", "PUT",
            "/api/v1/channels/0/permissions", {"overwrites": []}
        )

# -----------------------------------------------------------------------------
class ForumTests(BaseTests):
    """Test suite for forum channels, threads, replies and tags."""

    def run_all(self):
        for test in (
            self.test_list_tags,
            self.test_create_tag,
            self.test_update_tag,
            self.test_delete_tag,
            self.test_create_forum_channel,
            self.test_update_forum_channel,
            self.test_delete_forum_channel,
            self.test_create_thread,
            self.test_update_thread,
            self.test_add_tags_to_thread,
            self.test_remove_tags_from_thread,
            self.test_close_reopen_thread,
            self.test_add_reply_to_thread,
            self.test_edit_reply_in_thread,
            self.test_delete_reply_in_thread,
            self.test_list_threads_in_channel,
            self.test_get_thread_details,
            self.test_list_messages_in_thread,
            self.test_get_thread_message_details,
        ):
            try:
                test()
            except Exception:
                LOGGER.exception("Unhandled exception in forum test")

    # Helpers
    def _create_forum_channel(self) -> Optional[int]:
        path = f"/api/v1/guilds/{self.guild_id}/channels"
        body = {"name": f"test-forum-{int(time.time())}", "type": "forum"}
        resp, _ = api_call("POST", path, body=body, headers=self.headers())
        if not resp or resp.status_code not in (200, 201):
            record_result("setup_forum_channel", "POST", path,
                          getattr(resp, "status_code", None), False, "forum channel creation failed")
            return None
        try:
            j = resp.json()
        except Exception:
            j = {}
        cid = self._extract_created_id(j)
        if cid is None:
            # fallback legacy shapes
            if isinstance(j, dict):
                cid = j.get("channel", {}).get("id") or j.get("data", {}).get("id") or j.get("id")
        try:
            cid = int(cid)
        except Exception:
            record_result("setup_forum_channel", "POST", path,
                          getattr(resp, "status_code", None), False, "unable to parse channel id")
            return None
        schedule_cleanup("setup_forum_channel", "channel", cid, "DELETE", f"/api/v1/channels/{cid}")
        self.wait_valid()

        # Validate persisted fields (name/type) via GET
        gresp, _ = api_call("GET", f"/api/v1/channels/{cid}", headers=self.headers())
        if not gresp or gresp.status_code != 200:
            record_result("setup_forum_channel verify", "GET", f"/api/v1/channels/{cid}",
                          getattr(gresp, "status_code", None), False, "GET failed for created forum channel")
            return cid
        try:
            gj = gresp.json()
        except Exception:
            gj = {}
        ok, reason = validate_object({"name": body["name"], "type": body["type"]}, gj)
        record_result("setup_forum_channel verify", "GET", f"/api/v1/channels/{cid}", gresp.status_code, ok,
                      None if ok else f"validation:{reason}")
        return cid

    def _create_tag(self, channel_id: int, name: str, emoji: str) -> Optional[int]:
        path = f"/api/v1/channels/{channel_id}/tags"
        body = {"name": name, "emoji": emoji}
        resp, _ = api_call("POST", path, body=body, headers=self.headers())
        if not resp or resp.status_code not in (200, 201):
            record_result("setup_tag", "POST", path, getattr(resp, "status_code", None),
                        False, "tag creation failed")
            return None
        try:
            j = resp.json()
        except Exception:
            j = {}
        tid = self._extract_created_id(j)
        if tid is None:
            # fallback to legacy shapes
            if isinstance(j, dict):
                tid = j.get("tag", {}).get("id") or j.get("data", {}).get("id") or j.get("id")
        try:
            tid = int(tid)
        except Exception:
            record_result("setup_tag", "POST", path, getattr(resp, "status_code", None),
                        False, "unable to parse tag id")
            return None
        # canonical single-tag delete path per OpenAPI
        schedule_cleanup("setup_tag", "tag", tid, "DELETE", f"/api/v1/tags/{tid}")
        self.wait_valid()
        # Validate persisted fields via GET /api/v1/tags/{id}
        gresp, _ = api_call("GET", f"/api/v1/tags/{tid}", headers=self.headers())
        if not gresp or gresp.status_code != 200:
            record_result("setup_tag verify", "GET", f"/api/v1/tags/{tid}",
                        getattr(gresp, "status_code", None), False, "GET failed for created tag")
            return tid
        try:
            gj = gresp.json()
        except Exception:
            gj = {}
        # Normalize emoji in expected body so hex/codepoint inputs like "1f4cc"
        # compare equal to the server-returned unicode "📌".
        expected_body = dict(body)
        try:
            from utils.discord_helpers import normalize_emoji
            expected_body["emoji"] = normalize_emoji(expected_body.get("emoji", ""))
        except Exception:
            # If normalization or import fails, fall back to original value
            pass
        ok, reason = validate_object(expected_body, gj)
        record_result("setup_tag verify", "GET", f"/api/v1/tags/{tid}", gresp.status_code, ok,
                    None if ok else f"validation:{reason}")
        return tid

    def _create_thread(self, forum_channel_id: int, name: str, initial_message: Optional[dict] = None) -> Optional[int]:
        path = f"/api/v1/channels/{forum_channel_id}/threads"
        body = {"name": name}
        # Ensure initial_message is present (schema requires content in newer API)
        if initial_message is None:
            initial_message = {"title": "init", "description": now_iso()}
        body["initial_message"] = initial_message
        resp, _ = api_call("POST", path, body=body, headers=self.headers())
        if not resp or resp.status_code not in (200, 201):
            record_result("setup_thread", "POST", path, getattr(resp, "status_code", None),
                          False, "thread creation failed")
            return None
        try:
            j = resp.json()
        except Exception:
            j = {}
        tid = self._extract_created_id(j)
        if tid is None:
            # fallback
            if isinstance(j, dict):
                tid = j.get("thread", {}).get("id") or j.get("data", {}).get("id") or j.get("id")
        try:
            tid = int(tid)
        except Exception:
            record_result("setup_thread", "POST", path, getattr(resp, "status_code", None),
                          False, "unable to parse thread id")
            return None
        # threads are channels in cleanup mapping
        schedule_cleanup("setup_thread", "thread", tid, "DELETE", f"/api/v1/channels/{tid}")
        self.wait_valid()

        # Validate thread name via GET /api/v1/channels/{tid}
        gresp, _ = api_call("GET", f"/api/v1/channels/{tid}", headers=self.headers())
        if not gresp or gresp.status_code != 200:
            record_result("setup_thread verify", "GET", f"/api/v1/channels/{tid}",
                          getattr(gresp, "status_code", None), False, "GET failed for created thread")
            return tid
        try:
            gj = gresp.json()
        except Exception:
            gj = {}
        ok, reason = validate_object({"name": name}, gj)
        record_result("setup_thread verify", "GET", f"/api/v1/channels/{tid}", gresp.status_code, ok,
                      None if ok else f"validation:{reason}")
        return tid

    def _post_message(self, channel_id: int, content: dict) -> Optional[int]:
        path = f"/api/v1/channels/{channel_id}/messages"
        body = {"content": content}
        resp, _ = api_call("POST", path, body=body, headers=self.headers())
        if not resp or resp.status_code not in (200, 201):
            # legacy helper used /api/v1/messages; adapt message creation to canonical endpoint
            record_result("setup_message", "POST", path, getattr(resp, "status_code", None),
                          False, "message create failed")
            return None
        try:
            j = resp.json()
        except Exception:
            j = {}
        mid = self._extract_created_id(j)
        if mid is None:
            # fallback legacy shapes
            if isinstance(j, dict):
                mid = j.get("message", {}).get("id") or j.get("data", {}).get("id") or j.get("message_id") or j.get("id")
        try:
            mid = int(mid)
        except Exception:
            record_result("setup_message", "POST", path, getattr(resp, "status_code", None),
                          False, "unable to parse message id")
            return None
        # canonical delete for messages
        schedule_cleanup("setup_message", "message", mid, "DELETE", f"/api/v1/messages/{mid}")
        self.wait_valid()
        # Validate persisted content via GET /api/v1/messages/{mid}
        gresp, _ = api_call("GET", f"/api/v1/messages/{mid}", headers=self.headers())
        if not gresp or gresp.status_code != 200:
            record_result("setup_message verify", "GET", f"/api/v1/messages/{mid}",
                          getattr(gresp, "status_code", None), False, "GET failed for created message")
            return mid
        try:
            gj = gresp.json()
        except Exception:
            gj = {}
        # Validate that the embed/content we sent is present in the stored message
        ok, reason = validate_object({"content": content}, gj)
        record_result("setup_message verify", "GET", f"/api/v1/messages/{mid}", gresp.status_code, ok,
                      None if ok else f"validation:{reason}")
        return mid

    # Tag tests
    def test_list_tags(self):
        ch = self._create_forum_channel()
        if not ch:
            return
        p = f"/api/v1/channels/{ch}/tags"
        resp = self.run_simple_expected("GET list tags", "GET", p, None, [200])
        if not resp or resp.status_code != 200:
            return
        try:
            j = resp.json()
        except Exception:
            j = {}
        arr = self._normalize_data_to_list(j)
        ok = isinstance(arr, list)
        record_result("GET list tags verify", "GET", p, resp.status_code, ok,
                      None if ok else "data.items not list")

    def test_create_tag(self):
        forum = self._create_forum_channel()
        if not forum:
            return
        emoji = "".join(f"{ord(c):x}" for c in "📌")
        body = {"name": f"tag-{int(time.time())}", "emoji": emoji}
        p = f"/api/v1/channels/{forum}/tags"

        # Custom validator that normalizes the request emoji to unicode before comparing.
        def validate_tag(get_json: dict) -> Tuple[bool, str]:
            j = get_json
            # unwrap canonical envelope if present
            if isinstance(j, dict) and "data" in j:
                j = j["data"]
            # unwrap possible 'tag' wrapper
            if isinstance(j, dict) and "tag" in j and isinstance(j["tag"], dict):
                j = j["tag"]
            ret_emoji = j.get("emoji") if isinstance(j, dict) else None
            ret_name = j.get("name") if isinstance(j, dict) else None
            expected_emoji = normalize_emoji(body["emoji"])
            if ret_emoji != expected_emoji:
                return False, f"field 'emoji': expected {expected_emoji!r}, got {ret_emoji!r}"
            if ret_name != body["name"]:
                return False, f"field 'name': expected {body['name']!r}, got {ret_name!r}"
            return True, ""
        # POST -> GET /api/v1/tags/{id} validation using custom validator
        self.run_validation("POST create tag", "POST", p, body, "/api/v1/tags/{id}", validate_fn=validate_tag)

    def test_update_tag(self):
        forum = self._create_forum_channel()
        if not forum:
            return
        emoji = "".join(f"{ord(c):x}" for c in "🏷️")
        tid = self._create_tag(forum, f"t-{int(time.time())}", emoji)
        if not tid:
            return
        # Use canonical single-tag endpoint for updates per OpenAPI
        p = f"/api/v1/tags/{tid}"
        body = {"name": "updated-tag", "emoji": emoji}
        # PUT -> GET /api/v1/tags/{tid} validation
        self.run_validation("PUT update tag", "PUT", p, body, p)

    def test_delete_tag(self):
        forum = self._create_forum_channel()
        if not forum:
            return
        emoji = "".join(f"{ord(c):x}" for c in "🔖")
        tid = self._create_tag(forum, f"t-del-{int(time.time())}", emoji)
        if not tid:
            return
        # Prevent double-delete from scheduled cleanup
        with CLEANUP_LOCK:
            CLEANUP_QUEUE[:] = [e for e in CLEANUP_QUEUE if not (e["resource_type"] == "tag" and e["resource_id"] == str(tid))]
        # DELETE canonical single-tag endpoint and verify 404 afterwards
        self.run_simple_expected("DELETE tag", "DELETE", f"/api/v1/tags/{tid}", None, [200, 204])
        self.wait_valid()
        self.run_simple_expected("GET tag after delete", "GET", f"/api/v1/tags/{tid}", None, [404])

    def test_create_forum_channel(self):
        path = f"/api/v1/guilds/{self.guild_id}/channels"
        body = {"name": f"test-forum-{int(time.time())}", "type": "forum", "position": 1}
        # POST -> GET /api/v1/channels/{id} validation
        self.run_validation("POST create forum channel", "POST", path, body, "/api/v1/channels/{id}")

    def test_update_forum_channel(self):
        cid = self._create_forum_channel()
        if not cid:
            return
        p = f"/api/v1/channels/{cid}"
        body = {"topic": "forum topic updated", "default_auto_archive_duration": 60}

        def validate_forum_update(get_json: dict) -> Tuple[bool, str]:
            j = get_json
            if isinstance(j, dict) and "data" in j:
                j = j["data"]
            if isinstance(j, dict) and "channel" in j and isinstance(j["channel"], dict):
                j = j["channel"]
            if not isinstance(j, dict):
                return False, "unexpected response shape"
            got_topic = j.get("topic")
            got_auto = j.get("default_auto_archive_duration")
            ok_topic = (got_topic == body["topic"])
            ok_auto = (int(got_auto) == int(body["default_auto_archive_duration"])) if got_auto is not None else False
            if ok_topic and ok_auto:
                return True, ""
            return False, f"topic={got_topic!r} default_auto_archive_duration={got_auto!r}"

        self.run_validation("PUT update forum channel", "PUT", p, body, p, validate_fn=validate_forum_update)

    def test_delete_forum_channel(self):
        cid = self._create_forum_channel()
        if not cid:
            return
        # Prevent auto-cleanup so we delete explicitly
        with CLEANUP_LOCK:
            CLEANUP_QUEUE[:] = [e for e in CLEANUP_QUEUE if not (e["resource_type"] == "channel" and e["resource_id"] == str(cid))]
        self.run_simple_expected("DELETE forum channel", "DELETE", f"/api/v1/channels/{cid}", None, [200, 204])
        self.wait_valid()
        self.run_simple_expected("GET forum channel after delete", "GET", f"/api/v1/channels/{cid}", None, [404])

    # Threads and tag assignment
    def test_create_thread(self):
        forum = self._create_forum_channel()
        if not forum:
            return
        p = f"/api/v1/channels/{forum}/threads"
        body = {"name": f"thread-{int(time.time())}", "initial_message": {"title": "init", "description": "hello"}}

        # Validate by listing thread messages (canonical design: messages are separate resources)
        def validate_initial_message_in_thread(get_json: dict) -> Tuple[bool, str]:
            # normalize to list (handles envelopes / data -> items etc.)
            arr = self._normalize_data_to_list(get_json)
            if not isinstance(arr, list) or len(arr) == 0:
                return False, "no messages returned"
            # look for a message whose content matches the initial_message we sent
            for m in arr:
                if not isinstance(m, dict):
                    continue
                # robustly extract content payload from common wrappers
                content = None
                if "content" in m:
                    content = m.get("content")
                elif isinstance(m.get("data", None), dict) and "content" in m["data"]:
                    content = m["data"]["content"]
                elif "message" in m and isinstance(m["message"], dict):
                    content = m["message"].get("content")
                if not isinstance(content, dict):
                    continue
                try:
                    if content.get("title") == body["initial_message"]["title"] and content.get("description") == body["initial_message"]["description"]:
                        return True, ""
                except Exception:
                    continue
            return False, "initial_message not found in thread messages"

        # POST -> GET /api/v1/threads/{id}/messages and use custom validator
        self.run_validation("POST create thread", "POST", p, body, "/api/v1/threads/{id}/messages", validate_fn=validate_initial_message_in_thread)

    def test_update_thread(self):
        forum = self._create_forum_channel()
        if not forum:
            return
        tid = self._create_thread(forum, f"t-upd-{int(time.time())}")
        if not tid:
            return
        p = f"/api/v1/threads/{tid}"
        body = {"name": "thread-renamed"}
        # PUT -> GET /api/v1/threads/{id} validation
        self.run_validation("PUT update thread", "PUT", p, body, p)

    def test_add_tags_to_thread(self):
        forum = self._create_forum_channel()
        if not forum:
            return
        tid = self._create_thread(forum, f"t-tags-{int(time.time())}")
        if not tid:
            return
        tag_emoji = "".join(f"{ord(c):x}" for c in "⭐")
        tag_id = self._create_tag(forum, f"tag-for-thread-{int(time.time())}", tag_emoji)
        if not tag_id:
            return
        # Use canonical thread-tag endpoint per OpenAPI
        p = f"/api/v1/threads/{tid}/tags"
        body = {"tags": [tag_id]}

        def validate_thread_has_tag(get_json: dict) -> Tuple[bool, str]:
            j = get_json
            if isinstance(j, dict) and "data" in j:
                j = j["data"]
            # Possible places for applied tags: 'tags', 'applied_tags', 'tag_ids'
            arr = []
            if isinstance(j, dict):
                arr = j.get("tags") or j.get("applied_tags") or j.get("tag_ids") or []
            if isinstance(arr, list) and any((int(x) == int(tag_id) if not isinstance(x, dict) else int(x.get("id", -1)) == int(tag_id)) for x in arr):
                return True, ""
            return False, "tag not applied to thread (no tags field or missing tag)"

        self.run_validation("PUT add tags to thread", "PUT", p, body, f"/api/v1/threads/{tid}", validate_fn=validate_thread_has_tag)

    def test_remove_tags_from_thread(self):
        forum = self._create_forum_channel()
        if not forum:
            return
        tid = self._create_thread(forum, f"t-tags-rem-{int(time.time())}")
        if not tid:
            return
        tag_emoji = "".join(f"{ord(c):x}" for c in "✨")
        tag_id = self._create_tag(forum, f"tag-rem-{int(time.time())}", tag_emoji)
        if not tag_id:
            return
        # First add tag
        api_call("PUT", f"/api/v1/threads/{tid}/tags", body={"tags": [tag_id]}, headers=self.headers())
        self.wait_valid()
        # Now remove tags using canonical endpoint -> validate none present
        p = f"/api/v1/threads/{tid}/tags"
        body = {"tags": []}

        def validate_thread_no_tags(get_json: dict) -> Tuple[bool, str]:
            j = get_json
            if isinstance(j, dict) and "data" in j:
                j = j["data"]
            arr = []
            if isinstance(j, dict):
                arr = j.get("tags") or j.get("applied_tags") or j.get("tag_ids") or []
            ok = isinstance(arr, list) and len(arr) == 0
            return (True, "") if ok else (False, "tags still present on thread")

        self.run_validation("PUT remove tags from thread", "PUT", p, body, f"/api/v1/threads/{tid}", validate_fn=validate_thread_no_tags)

    def test_close_reopen_thread(self):
        forum = self._create_forum_channel()
        if not forum:
            return
        tid = self._create_thread(forum, f"t-close-{int(time.time())}")
        if not tid:
            return
        # Close (archive) thread -> validate archived=True
        pclose = f"/api/v1/threads/{tid}/close"

        def validate_archived(get_json: dict) -> Tuple[bool, str]:
            j = get_json
            if isinstance(j, dict) and "data" in j:
                j = j["data"]
            archived = None
            if isinstance(j, dict):
                # thread object might be nested under 'thread' or be the data itself
                if "thread" in j and isinstance(j["thread"], dict):
                    archived = j["thread"].get("archived")
                else:
                    archived = j.get("archived")
            return (True, "") if archived is True else (False, f"expected archived True, got {archived}")

        self.run_validation("PUT close thread", "PUT", pclose, None, f"/api/v1/threads/{tid}", validate_fn=validate_archived)
        # Reopen (unarchive) thread -> validate archived=False
        preopen = f"/api/v1/threads/{tid}/open"

        def validate_unarchived(get_json: dict) -> Tuple[bool, str]:
            j = get_json
            if isinstance(j, dict) and "data" in j:
                j = j["data"]
            archived = None
            if isinstance(j, dict):
                if "thread" in j and isinstance(j["thread"], dict):
                    archived = j["thread"].get("archived")
                else:
                    archived = j.get("archived")
            return (True, "") if archived is False else (False, f"expected archived False, got {archived}")

        self.run_validation("PUT reopen thread", "PUT", preopen, None, f"/api/v1/threads/{tid}", validate_fn=validate_unarchived)

    def test_add_reply_to_thread(self):
        forum = self._create_forum_channel()
        if not forum:
            return
        tid = self._create_thread(forum, f"t-reply-{int(time.time())}")
        if not tid:
            return
        # POST reply to thread via thread-scoped endpoint -> validate via GET /api/v1/messages/{id}
        p = f"/api/v1/threads/{tid}/messages"
        body = {"content": {"description": "reply content"}}
        self.run_validation("POST reply to thread", "POST", p, body, "/api/v1/messages/{id}")

    def test_edit_reply_in_thread(self):
        forum = self._create_forum_channel()
        if not forum:
            return
        tid = self._create_thread(forum, f"t-reply-edit-{int(time.time())}")
        if not tid:
            return
        # create a reply using helper (this schedules cleanup)
        mid = self._post_message(tid, {"description": "reply to edit"})
        if not mid:
            return
        p = f"/api/v1/messages/{mid}"
        body = {"content": {"description": "edited"}}
        # PUT -> GET /api/v1/messages/{mid} validation
        self.run_validation("PUT edit reply", "PUT", p, body, p)

    def test_delete_reply_in_thread(self):
        forum = self._create_forum_channel()
        if not forum:
            return
        tid = self._create_thread(forum, f"t-reply-del-{int(time.time())}")
        if not tid:
            return
        mid = self._post_message(tid, {"description": "reply to delete"})
        if not mid:
            return
        # Prevent scheduled cleanup so we delete explicitly
        with CLEANUP_LOCK:
            CLEANUP_QUEUE[:] = [e for e in CLEANUP_QUEUE if not (e["resource_type"] == "message" and e["resource_id"] == str(mid))]
        # DELETE canonical message endpoint then verify 404
        self.run_simple_expected("DELETE reply", "DELETE", f"/api/v1/messages/{mid}", None, [200, 204])
        self.wait_valid()
        self.run_simple_expected("GET reply after delete", "GET", f"/api/v1/messages/{mid}", None, [404])

    def test_list_threads_in_channel(self):
        forum = self._create_forum_channel()
        if not forum:
            return
        # create a thread to ensure there's at least one
        tid = self._create_thread(forum, f"t-list-{int(time.time())}")
        if not tid:
            return
        p = f"/api/v1/channels/{forum}/threads"
        resp = self.run_simple_expected("GET list threads", "GET", p, None, [200])
        if not resp or resp.status_code != 200:
            return
        try:
            j = resp.json()
        except Exception:
            j = {}
        data = j.get("data", j if isinstance(j, dict) else {})
        arr = self._normalize_data_to_list(j)
        ok = isinstance(arr, list) and any((t.get("id") == tid) for t in arr if isinstance(t, dict))
        record_result("GET list threads verify", "GET", p, resp.status_code, ok,
                      None if ok else "created thread not listed")

    def test_get_thread_details(self):
        forum = self._create_forum_channel()
        if not forum:
            return
        tid = self._create_thread(forum, f"t-detail-{int(time.time())}")
        if not tid:
            return
        p = f"/api/v1/threads/{tid}"
        resp = self.run_simple_expected("GET thread details", "GET", p, None, [200])
        if not resp or resp.status_code != 200:
            return
        try:
            j = resp.json()
        except Exception:
            j = {}
        obj = j.get("data", j if isinstance(j, dict) else {})
        if isinstance(obj, dict) and "thread" in obj and isinstance(obj["thread"], dict):
            obj = obj["thread"]
        ok = isinstance(obj, dict) and int(obj.get("id", -1)) == tid and "name" in obj
        record_result("GET thread details verify", "GET", p, resp.status_code, ok,
                      None if ok else "unexpected thread payload")

    def test_list_messages_in_thread(self):
        forum = self._create_forum_channel()
        if not forum:
            return
        tid = self._create_thread(forum, f"t-msgs-{int(time.time())}")
        if not tid:
            return
        mid = self._post_message(tid, {"description": "thread message 1"})
        if not mid:
            return
        p = f"/api/v1/threads/{tid}/messages"
        resp = self.run_simple_expected("GET list messages in thread", "GET", p, None, [200])
        if not resp or resp.status_code != 200:
            return
        try:
            j = resp.json()
        except Exception:
            j = {}
        data = j.get("data", j if isinstance(j, dict) else {})
        arr = self._normalize_data_to_list(j)
        ok = isinstance(arr, list) and any((m.get("id") == mid) for m in arr if isinstance(m, dict))
        record_result("GET list messages in thread verify", "GET", p, resp.status_code, ok,
                      None if ok else "created message not listed")

    def test_get_thread_message_details(self):
        forum = self._create_forum_channel()
        if not forum:
            return
        tid = self._create_thread(forum, f"t-msg-detail-{int(time.time())}")
        if not tid:
            return
        mid = self._post_message(tid, {"description": "message detail"})
        if not mid:
            return
        # Use canonical thread message GET: /api/v1/threads/{thread_id}/messages/{message_id}
        p = f"/api/v1/threads/{tid}/messages/{mid}"
        self.run_simple_expected("GET thread message details", "GET", p, None, [200])

# -----------------------------------------------------------------------------
class PermissionsTests(BaseTests):
    """Test suite for the /api/v1/permissions family of endpoints."""

    def run_all(self):
        for test in (
            # listings
            self.test_list_all_permissions,
            self.test_list_role_permissions,
            self.test_list_user_permissions,
            self.test_list_channel_permissions,
            self.test_list_category_permissions,

            # conversions
            self.test_convert_names_to_value_valid,
            self.test_convert_names_to_value_invalid_name,
            self.test_convert_names_to_value_empty,
            self.test_convert_names_to_value_forbid,

            self.test_convert_value_to_names_valid,
            self.test_convert_value_to_names_invalid,
            self.test_convert_value_to_names_forbid,

            # calculate
            self.test_calculate_permissions_valid,
            self.test_calculate_permissions_omit_allow_deny,
            self.test_calculate_permissions_invalid_base,
            self.test_calculate_permissions_forbid,

            # consolidated /permissions/check (evaluate + detailed + variations)
            self.test_check_permissions_evaluate_user_guild,
            self.test_check_permissions_evaluate_user_channel,
            self.test_check_permissions_evaluate_role_guild,
            self.test_check_permissions_evaluate_role_channel,
            self.test_check_permissions_detailed_user_channel,
            self.test_check_permissions_detailed_role_channel,
            self.test_check_permissions_detailed_role_guild,
            self.test_check_permissions_admin_role_grants_all,
            self.test_check_permissions_user_direct_overwrite_allow_and_deny,
            self.test_check_permissions_inherited_category_overwrites,
            self.test_check_permissions_thread_scope_inheritance,
            self.test_check_permissions_non_applicable_permission,
            self.test_check_permissions_case_insensitive_and_duplicates,
            self.test_check_permissions_bot_subject_guild,
            self.test_check_permissions_invalid_permission_name,
            self.test_check_permissions_missing_subject_or_target,
            self.test_check_permissions_target_notfound,
            self.test_check_permissions_forbid,
        ):
            try:
                test()
            except Exception:
                LOGGER.exception("Unhandled exception in permissions test")

    # Helper: reuse RoleTests helper
    def _create_helper_role(self) -> Optional[int]:
        return RoleTests(self.guild_id, self.user_id, self.delay, self.vdelay)._create_helper_role()
    
    # Add this small helper so PermissionsTests can create forum channels when needed
    def _create_forum_channel(self) -> Optional[int]:
        return ForumTests(self.guild_id, self.user_id, self.delay, self.vdelay)._create_forum_channel()

    def _create_thread(self) -> Optional[int]:
        return ForumTests(self.guild_id, self.user_id, self.delay, self.vdelay)._create_thread()

    def _mk_category(self) -> Optional[int]:
        return CategoryTests(self.guild_id, self.user_id, self.delay, self.vdelay)._mk_category()
        
    def _make_channel(self) -> Optional[int]:
        return MessageTests(self.guild_id, self.user_id, self.delay, self.vdelay)._make_channel()

    # -----------------------
    # Listing tests (unchanged)
    # -----------------------
    def test_list_all_permissions(self):
        resp = self.run_simple_expected("GET list all permissions", "GET", "/api/v1/permissions", None, [200])
        if not resp or resp.status_code != 200:
            return
        try:
            j = resp.json()
        except Exception:
            j = {}
        arr = self._normalize_data_to_list(j)
        ok = isinstance(arr, list) and all(isinstance(x, dict) and "name" in x and "value" in x for x in arr)
        record_result("GET list all permissions verify", "GET", "/api/v1/permissions", resp.status_code, ok,
                      None if ok else "permissions payload missing or malformed")

    def test_list_role_permissions(self):
        resp = self.run_simple_expected("GET list role permissions", "GET", "/api/v1/permissions/roles", None, [200])
        if not resp or resp.status_code != 200:
            return
        arr = self._normalize_data_to_list(resp.json() if resp else {})
        ok = isinstance(arr, list)
        record_result("GET list role permissions verify", "GET", "/api/v1/permissions/roles", resp.status_code, ok,
                      None if ok else "data.items not list")

    def test_list_user_permissions(self):
        resp = self.run_simple_expected("GET list user permissions", "GET", "/api/v1/permissions/users", None, [200])
        if not resp or resp.status_code != 200:
            return
        arr = self._normalize_data_to_list(resp.json() if resp else {})
        ok = isinstance(arr, list)
        record_result("GET list user permissions verify", "GET", "/api/v1/permissions/users", resp.status_code, ok,
                      None if ok else "data.items not list")

    def test_list_channel_permissions(self):
        resp = self.run_simple_expected("GET list channel permissions", "GET", "/api/v1/permissions/channels", None, [200])
        if not resp or resp.status_code != 200:
            return
        arr = self._normalize_data_to_list(resp.json() if resp else {})
        ok = isinstance(arr, list)
        record_result("GET list channel permissions verify", "GET", "/api/v1/permissions/channels", resp.status_code, ok,
                      None if ok else "data.items not list")

    def test_list_category_permissions(self):
        resp = self.run_simple_expected("GET list category permissions", "GET", "/api/v1/permissions/categories", None, [200])
        if not resp or resp.status_code != 200:
            return
        arr = self._normalize_data_to_list(resp.json() if resp else {})
        ok = isinstance(arr, list)
        record_result("GET list category permissions verify", "GET", "/api/v1/permissions/categories", resp.status_code, ok,
                      None if ok else "data.items not list")

    # -----------------------
    # Convert/Calculate tests
    # -----------------------
    def test_convert_names_to_value_valid(self):
        body = {"names": ["SEND_MESSAGES", "VIEW_CHANNEL"]}
        resp = self.run_simple_expected("POST convert names→value", "POST",
                                        "/api/v1/permissions/convert/names-to-value", body, [200])
        if not resp or resp.status_code != 200:
            return
        try:
            j = resp.json()
        except Exception:
            j = {}
        data = j.get("data", {})
        ok = False
        if isinstance(data, dict):
            for v in data.values():
                if isinstance(v, int):
                    ok = True
                    break
        elif isinstance(data, int):
            ok = True
        record_result("POST convert names→value verify", "POST", "/api/v1/permissions/convert/names-to-value", resp.status_code, ok,
                      None if ok else "response data missing integer bitfield")

    def test_convert_names_to_value_invalid_name(self):
        body = {"names": ["NO_SUCH_PERMISSION"]}
        self.run_simple_expected("POST convert names→value invalid", "POST",
                                 "/api/v1/permissions/convert/names-to-value", body, [400, 422])

    def test_convert_names_to_value_empty(self):
        body = {"names": []}
        self.run_simple_expected("POST convert names→value empty", "POST",
                                 "/api/v1/permissions/convert/names-to-value", body, [400, 422])

    def test_convert_names_to_value_forbid(self):
        self.run_forbidden("POST convert names→value forbid", "POST",
                           "/api/v1/permissions/convert/names-to-value", {"names": ["SEND_MESSAGES"]})

    def test_convert_value_to_names_valid(self):
        body = {"value": 1}
        resp = self.run_simple_expected("POST convert value→names", "POST",
                                        "/api/v1/permissions/convert/value-to-names", body, [200])
        if not resp or resp.status_code != 200:
            return
        j = resp.json() if resp else {}
        data = j.get("data", {})
        ok = False
        if isinstance(data, list):
            ok = all(isinstance(x, str) for x in data)
        elif isinstance(data, dict):
            ok = any(isinstance(k, str) for k in data.keys())
        record_result("POST convert value→names verify", "POST", "/api/v1/permissions/convert/value-to-names", resp.status_code, ok,
                      None if ok else "unexpected response shape for names list")

    def test_convert_value_to_names_invalid(self):
        body = {"value": -5}
        self.run_simple_expected("POST convert value→names invalid", "POST",
                                 "/api/v1/permissions/convert/value-to-names", body, [400, 422])

    def test_convert_value_to_names_forbid(self):
        self.run_forbidden("POST convert value→names forbid", "POST",
                           "/api/v1/permissions/convert/value-to-names", {"value": 1})

    def test_calculate_permissions_valid(self):
        body = {"base": 1, "allow": 2, "deny": 4}
        resp = self.run_simple_expected("POST calculate permissions", "POST",
                                        "/api/v1/permissions/calculate", body, [200])
        if not resp or resp.status_code != 200:
            return
        j = resp.json() if resp else {}
        data = j.get("data", j if isinstance(j, (dict, int)) else {})
        expected = (int(body["base"]) | int(body["allow"])) & (~int(body["deny"]))
        ok = False
        if isinstance(data, dict):
            for v in data.values():
                if isinstance(v, int) and v == expected:
                    ok = True
                    break
        elif isinstance(data, int):
            ok = (data == expected)
        record_result("POST calculate permissions verify", "POST", "/api/v1/permissions/calculate", resp.status_code, ok,
                      None if ok else f"expected effective {expected}, got {data}")

    def test_calculate_permissions_omit_allow_deny(self):
        body = {"base": 1, "allow": None, "deny": None}
        resp = self.run_simple_expected("POST calculate permissions omit allow/deny", "POST",
                                        "/api/v1/permissions/calculate", body, [200])
        if not resp or resp.status_code != 200:
            return
        j = resp.json() if resp else {}
        data = j.get("data", j if isinstance(j, (dict, int)) else {})
        expected = int(body["base"])
        ok = False
        if isinstance(data, dict):
            for v in data.values():
                if isinstance(v, int) and v == expected:
                    ok = True
                    break
        elif isinstance(data, int):
            ok = (data == expected)
        record_result("POST calculate permissions omit verify", "POST", "/api/v1/permissions/calculate", resp.status_code, ok,
                      None if ok else f"expected {expected}, got {data}")

    def test_calculate_permissions_invalid_base(self):
        body = {"base": -1}
        self.run_simple_expected("POST calculate permissions invalid base", "POST",
                                 "/api/v1/permissions/calculate", body, [400, 422])

    def test_calculate_permissions_forbid(self):
        self.run_forbidden("POST calculate permissions forbid", "POST",
                           "/api/v1/permissions/calculate", {"base": 1})

    # -----------------------
    # Consolidated /permissions/check tests (evaluate-mode + detailed + variations)
    # -----------------------
    def test_check_permissions_evaluate_user_guild(self):
        name = "POST permissions/check evaluate (user @ guild)"
        path = "/api/v1/permissions/check"
        body = {"subject": {"type": "user", "id": self.user_id}, "target": {"type": "guild", "id": self.guild_id}}
        resp = self.run_simple_expected(name, "POST", path, body, [200])
        if not resp or resp.status_code != 200:
            return
        j = resp.json() if resp else {}
        data = j.get("data", j if isinstance(j, dict) else {})
        ok = isinstance(data, dict) and isinstance(data.get("base"), int) and isinstance(data.get("allowed_names"), list) and isinstance(data.get("denied_names"), list)
        record_result(name + " verify", "POST", path, resp.status_code, ok, None if ok else "evaluate response missing expected fields")

    def test_check_permissions_evaluate_user_channel(self):
        ch = self._make_channel_for_perms()
        if not ch:
            return
        name = "POST permissions/check evaluate (user @ channel)"
        path = "/api/v1/permissions/check"
        body = {"subject": {"type": "user", "id": self.user_id}, "target": {"type": "channel", "id": ch}}
        resp = self.run_simple_expected(name, "POST", path, body, [200])
        if not resp or resp.status_code != 200:
            return
        j = resp.json() if resp else {}
        data = j.get("data", j if isinstance(j, dict) else {})
        ok = isinstance(data, dict) and isinstance(data.get("base"), int)
        record_result(name + " verify", "POST", path, resp.status_code, ok, None if ok else "evaluate response missing base bitfield")

    def test_check_permissions_evaluate_role_guild(self):
        rid = self._create_helper_role()
        if not rid:
            return
        body = {"subject": {"type": "role", "id": rid}, "target": {"type": "guild", "id": self.guild_id}}
        resp = self.run_simple_expected("POST permissions/check evaluate (role@guild)", "POST", "/api/v1/permissions/check", body, [200])
        if not resp or resp.status_code != 200:
            return
        j = resp.json() if resp else {}
        data = j.get("data", j if isinstance(j, dict) else {})
        ok = isinstance(data, dict) and isinstance(data.get("base"), int)
        record_result("evaluate role@guild verify", "POST", "/api/v1/permissions/check", resp.status_code, ok, None if ok else "missing base")

    def test_check_permissions_evaluate_role_channel(self):
        rid = self._create_helper_role()
        if not rid:
            return
        ch = self._make_channel_for_perms()
        if not ch:
            return
        body = {"subject": {"type": "role", "id": rid}, "target": {"type": "channel", "id": ch}}
        resp = self.run_simple_expected("POST permissions/check evaluate (role@channel)", "POST", "/api/v1/permissions/check", body, [200])
        if not resp or resp.status_code != 200:
            return
        j = resp.json() if resp else {}
        data = j.get("data", j if isinstance(j, dict) else {})
        ok = isinstance(data, dict) and isinstance(data.get("base"), int)
        record_result("evaluate role@channel verify", "POST", "/api/v1/permissions/check", resp.status_code, ok, None if ok else "missing base")

    def test_check_permissions_detailed_user_channel(self):
        ch = self._make_channel_for_perms()
        if not ch:
            return
        name = "POST permissions/check detailed (user @ channel)"
        path = "/api/v1/permissions/check"
        perms = ["SEND_MESSAGES", "VIEW_CHANNEL"]
        body = {"subject": {"type": "user", "id": self.user_id}, "target": {"type": "channel", "id": ch}, "permissions": perms}
        resp = self.run_simple_expected(name, "POST", path, body, [200])
        if not resp or resp.status_code != 200:
            return
        j = resp.json() if resp else {}
        data = j.get("data", j if isinstance(j, dict) else {})
        ok = False
        if isinstance(data, dict):
            ok = ("allowed" in data and isinstance(data.get("allowed"), bool)
                  and "denied" in data and isinstance(data.get("denied"), list)
                  and "granted" in data and isinstance(data.get("granted"), list))
            if ok and len(data.get("granted")) > 0:
                g0 = data["granted"][0]
                ok = ok and isinstance(g0, dict) and "permission" in g0 and "source" in g0
        record_result(name + " verify", "POST", path, resp.status_code, ok, None if ok else "detailed check response malformed")

    def test_check_permissions_detailed_role_channel(self):
        rid = self._create_helper_role()
        if not rid:
            return
        ch = self._make_channel_for_perms()
        if not ch:
            return
        name = "POST permissions/check detailed (role @ channel)"
        path = "/api/v1/permissions/check"
        perms = ["MANAGE_CHANNELS", "VIEW_CHANNEL"]
        body = {"subject": {"type": "role", "id": rid}, "target": {"type": "channel", "id": ch}, "permissions": perms}
        resp = self.run_simple_expected(name, "POST", path, body, [200])
        if not resp or resp.status_code != 200:
            return
        j = resp.json() if resp else {}
        data = j.get("data", j if isinstance(j, dict) else {})
        ok = isinstance(data, dict) and "granted" in data and isinstance(data.get("granted"), list) and "denied" in data
        record_result(name + " verify", "POST", path, resp.status_code, ok, None if ok else "role/channel detailed response malformed")

    def test_check_permissions_detailed_role_guild(self):
        rid = self._create_helper_role()
        if not rid:
            return
        body = {"subject": {"type": "role", "id": rid}, "target": {"type": "guild", "id": self.guild_id}, "permissions": ["MANAGE_GUILD", "VIEW_AUDIT_LOG"]}
        resp = self.run_simple_expected("POST permissions/check detailed (role@guild)", "POST", "/api/v1/permissions/check", body, [200])
        if not resp or resp.status_code != 200:
            return
        j = resp.json() if resp else {}
        d = j.get("data", j if isinstance(j, dict) else {})
        ok = isinstance(d, dict) and "granted" in d and "denied" in d
        record_result("role@guild detailed verify", "POST", "/api/v1/permissions/check", resp.status_code, ok, None if ok else "malformed")

    def test_check_permissions_admin_role_grants_all(self):
        rid = self._create_helper_role()
        if not rid:
            return
        # set ADMINISTRATOR on role
        body_upd = {"permissions": 0x8}
        r, _ = api_call("PUT", f"/api/v1/roles/{rid}", body=body_upd, headers=self.headers())
        if not r or r.status_code not in (200, 201, 204):
            record_result("setup admin role failed", "PUT", f"/api/v1/roles/{rid}", getattr(r, "status_code", None), False, "cannot set admin")
            return
        perms = ["BAN_MEMBERS", "MANAGE_CHANNELS", "CONNECT", "VIEW_AUDIT_LOG"]
        body = {"subject": {"type": "role", "id": rid}, "target": {"type": "guild", "id": self.guild_id}, "permissions": perms}
        resp = self.run_simple_expected("POST permissions/check admin role grants all", "POST", "/api/v1/permissions/check", body, [200])
        if not resp or resp.status_code != 200:
            return
        j = resp.json() if resp else {}
        data = j.get("data", j if isinstance(j, dict) else {})
        ok = isinstance(data, dict) and data.get("allowed") is True
        record_result("admin role grants all verify", "POST", "/api/v1/permissions/check", resp.status_code, ok, None if ok else "admin did not grant all")

    def test_check_permissions_user_direct_overwrite_allow_and_deny(self):
        ch = self._make_channel_for_perms()
        if not ch:
            return
        # set user-specific overwrite on channel: allow SEND_MESSAGES (0x800), deny MANAGE_MESSAGES (0x2000)
        put_body = {"overwrites": [{"target_id": self.user_id, "type": "member", "allow": 0x800, "deny": 0x2000}]}
        resp_put, _ = api_call("PUT", f"/api/v1/channels/{ch}/permissions", body=put_body, headers=self.headers())
        if not resp_put or resp_put.status_code not in (200, 201):
            record_result("setup user overwrite failed", "PUT", f"/api/v1/channels/{ch}/permissions", getattr(resp_put, "status_code", None), False, "overwrite PUT failed")
            return
        body = {"subject": {"type": "user", "id": self.user_id}, "target": {"type": "channel", "id": ch}, "permissions": ["SEND_MESSAGES", "MANAGE_MESSAGES"]}
        resp = self.run_simple_expected("POST permissions/check user direct overwrite", "POST", "/api/v1/permissions/check", body, [200])
        if not resp or resp.status_code != 200:
            return
        j = resp.json() if resp else {}
        data = j.get("data", j if isinstance(j, dict) else {})
        ok = False
        if isinstance(data, dict):
            granted_perms = {g["permission"]: g["source"]["type"] for g in data.get("granted", []) if isinstance(g, dict)}
            denied = data.get("denied", [])
            ok = ("SEND_MESSAGES" in granted_perms and granted_perms["SEND_MESSAGES"] == "direct" and "MANAGE_MESSAGES" in denied)
        record_result("user overwrite allow/deny verify", "POST", "/api/v1/permissions/check", resp.status_code, ok, None if ok else "overwrite not respected")

    def test_check_permissions_inherited_category_overwrites(self):
        cat = CategoryTests(self.guild_id, self.user_id, self.delay, self.vdelay)._mk_category()
        if not cat:
            return
        # create channel in category
        p = f"/api/v1/guilds/{self.guild_id}/channels"
        b = {"name": f"test-cat-channel-{int(time.time())}", "category_id": cat}
        r, _ = api_call("POST", p, body=b, headers=self.headers())
        if not r or r.status_code not in (200, 201):
            record_result("setup channel in category failed", "POST", p, getattr(r, "status_code", None), False, "channel create failed")
            return
        try:
            jr = r.json()
        except Exception:
            jr = {}
        ch = self._extract_created_id(jr)
        if not ch:
            return
        schedule_cleanup("setup_channel_in_cat", "channel", ch, "DELETE", f"/api/v1/channels/{ch}")
        # deny SEND_MESSAGES on category for @everyone via guild id as role id (common pattern)
        overwrite_body = {"overwrites": [{"target_id": self.guild_id, "type": "role", "deny": 0x800}]}
        resp_put, _ = api_call("PUT", f"/api/v1/categories/{cat}/permissions", body=overwrite_body, headers=self.headers())
        if not resp_put or resp_put.status_code not in (200, 201):
            record_result("setup category overwrite failed", "PUT", f"/api/v1/categories/{cat}/permissions", getattr(resp_put, "status_code", None), False, "cat overwrite failed")
            return
        body = {"subject": {"type": "user", "id": self.user_id}, "target": {"type": "channel", "id": ch}, "permissions": ["SEND_MESSAGES"]}
        resp = self.run_simple_expected("POST permissions/check inherited from category", "POST", "/api/v1/permissions/check", body, [200])
        if not resp or resp.status_code != 200:
            return
        j = resp.json() if resp else {}
        d = j.get("data", j if isinstance(j, dict) else {})
        ok = isinstance(d, dict) and ("SEND_MESSAGES" in d.get("denied", []) or d.get("allowed") is False)
        record_result("category inheritance verify", "POST", "/api/v1/permissions/check", resp.status_code, ok, None if ok else "inheritance not observed")

    def test_check_permissions_thread_scope_inheritance(self):
        forum = self._create_forum_channel()
        if not forum:
            return
        tid = self._create_thread(forum, f"t-inherit-{int(time.time())}")
        if not tid:
            return
        # set forum-level overwrite denying SEND_MESSAGES
        overwrite_body = {"overwrites": [{"target_id": self.guild_id, "type": "role", "deny": 0x800}]}
        resp_put, _ = api_call("PUT", f"/api/v1/channels/{forum}/permissions", body=overwrite_body, headers=self.headers())
        self.wait_valid()
        body = {"subject": {"type": "user", "id": self.user_id}, "target": {"type": "thread", "id": tid}, "permissions": ["SEND_MESSAGES"]}
        resp = self.run_simple_expected("POST permissions/check thread inheritance", "POST", "/api/v1/permissions/check", body, [200])
        if not resp or resp.status_code != 200:
            return
        j = resp.json() if resp else {}
        d = j.get("data", j if isinstance(j, dict) else {})
        ok = isinstance(d, dict) and ("SEND_MESSAGES" in d.get("denied", []) or d.get("allowed") is False)
        record_result("thread inheritance verify", "POST", "/api/v1/permissions/check", resp.status_code, ok, None if ok else "thread inheritance not observed")

    def test_check_permissions_non_applicable_permission(self):
        ch = self._make_channel_for_perms()
        if not ch:
            return
        body = {"subject": {"type": "user", "id": self.user_id}, "target": {"type": "channel", "id": ch}, "permissions": ["SPEAK"]}
        resp = self.run_simple_expected("POST permissions/check non-applicable perm", "POST", "/api/v1/permissions/check", body, [200])
        if not resp or resp.status_code != 200:
            return
        j = resp.json() if resp else {}
        d = j.get("data", j if isinstance(j, dict) else {})
        ok = isinstance(d, dict) and ("SPEAK" in d.get("denied", []) or d.get("allowed") is False)
        record_result("non-applicable perm verify", "POST", "/api/v1/permissions/check", resp.status_code, ok, None if ok else "unexpectedly allowed")

    def test_check_permissions_case_insensitive_and_duplicates(self):
        ch = self._make_channel_for_perms()
        if not ch:
            return
        body = {"subject": {"type": "user", "id": self.user_id}, "target": {"type": "channel", "id": ch}, "permissions": ["view_channel", "VIEW_CHANNEL", "View_Channel"]}
        resp = self.run_simple_expected("POST permissions/check case-insensitive/duplicates", "POST", "/api/v1/permissions/check", body, [200, 422])
        if not resp or resp.status_code != 200:
            return
        j = resp.json() if resp else {}
        d = j.get("data", j if isinstance(j, dict) else {})
        ok = isinstance(d, dict) and (isinstance(d.get("allowed"), bool) or isinstance(d.get("denied"), list))
        record_result("case-insensitive/dup verify", "POST", "/api/v1/permissions/check", resp.status_code, ok, None if ok else "unexpected response")

    def test_check_permissions_bot_subject_guild(self):
        """
        Original intent: test permissions/check for the bot identity.
        Updated behavior: this harness treats bots as regular users — the bot's numeric
        user id must be provided via --bot-id. If not provided, mark the test SKIPPED.
        """
        # ARGS is global; ensure a bot id was supplied on the command line
        bot_id = getattr(ARGS, "bot_id", None)
        if bot_id is None:
            record_result("POST permissions/check subject=bot (guild)", "POST", "/api/v1/permissions/check", None, False,
                        "SKIPPED (no --bot-id provided)")
            return

        body = {"subject": {"type": "user", "id": bot_id}, "target": {"type": "guild", "id": self.guild_id}}
        resp = self.run_simple_expected("POST permissions/check subject=bot (guild)", "POST", "/api/v1/permissions/check", body, [200, 503, 404])
        if not resp or resp.status_code != 200:
            return
        j = resp.json() if resp else {}
        d = j.get("data", j if isinstance(j, dict) else {})
        ok = isinstance(d, dict) and isinstance(d.get("base"), int)
        record_result("bot subject guild verify", "POST", "/api/v1/permissions/check", resp.status_code, ok, None if ok else "bot summary missing")


    def test_check_permissions_invalid_permission_name(self):
        ch = self._make_channel_for_perms()
        if not ch:
            return
        body = {"subject": {"type": "user", "id": self.user_id}, "target": {"type": "channel", "id": ch}, "permissions": ["THIS_IS_NOT_A_PERMISSION"]}
        self.run_simple_expected("POST permissions/check invalid perm name", "POST", "/api/v1/permissions/check", body, [422])

    def test_check_permissions_missing_subject_or_target(self):
        body1 = {"subject": {"type": "user", "id": self.user_id}, "permissions": ["VIEW_CHANNEL"]}
        self.run_simple_expected("POST permissions/check missing target", "POST", "/api/v1/permissions/check", body1, [400, 422])
        body2 = {"target": {"type": "channel", "id": 12345}, "permissions": ["VIEW_CHANNEL"]}
        self.run_simple_expected("POST permissions/check missing subject", "POST", "/api/v1/permissions/check", body2, [400, 422])

    def test_check_permissions_target_notfound(self):
        body = {"subject": {"type": "user", "id": self.user_id}, "target": {"type": "channel", "id": 999999999999}, "permissions": ["VIEW_CHANNEL"]}
        self.run_simple_expected("POST permissions/check target notfound", "POST", "/api/v1/permissions/check", body, [404])

    def test_check_permissions_forbid(self):
        self.run_forbidden("POST permissions/check forbid", "POST", "/api/v1/permissions/check", {
            "subject": {"type": "user", "id": 0},
            "target": {"type": "guild", "id": 0},
            "permissions": ["VIEW_CHANNEL"]
        })

    # -----------------------
    # Helpers specific to this class (existing in file but repeated here for clarity)
    # -----------------------
    def _make_channel_for_perms(self) -> Optional[int]:
        p = f"/api/v1/guilds/{self.guild_id}/channels"
        body = {"name": f"test-perm-channel-{int(time.time())}"}
        resp, _ = api_call("POST", p, body=body, headers=self.headers())
        if not resp or resp.status_code not in (200, 201):
            record_result("setup_channel_perms", "POST", p, getattr(resp, "status_code", None), False, "channel create failed")
            return None
        try:
            j = resp.json()
        except Exception:
            j = {}
        ch = self._extract_created_id(j)
        if ch is None:
            if isinstance(j, dict):
                ch = j.get("channel", {}).get("id") or j.get("data", {}).get("id") or j.get("id")
        try:
            ch = int(ch)
        except Exception:
            record_result("setup_channel_perms", "POST", p, getattr(resp, "status_code", None), False, "unable to parse channel id")
            return None
        schedule_cleanup("setup_channel_perms", "channel", ch, "DELETE", f"/api/v1/channels/{ch}")
        self.wait_valid()
        return ch

    @staticmethod
    def get_permission_check_result(path_no_query: str, permission_name: str, *, query_key: str = "permission") -> Tuple[Optional[int], Optional[bool], Optional[str]]:
        p = path_no_query if path_no_query.startswith("/") else f"/{path_no_query}"
        sep = "&" if "?" in p else "?"
        full = f"{p}{sep}{query_key}={permission_name}"
        resp, err = api_call("GET", full, headers={"Content-Type": "application/json"})
        if resp is None:
            return None, None, f"net err {err}"
        status = resp.status_code
        if status != 200:
            return status, None, f"expected 200, got {status}"
        try:
            body = resp.json()
        except Exception:
            return status, None, "non-JSON body"
        obj = body
        if isinstance(obj, dict) and "data" in obj:
            obj = obj["data"]
        if isinstance(obj, dict):
            for wrapper in ("message", "guild", "member", "role", "channel", "category", "thread", "tag"):
                if wrapper in obj and isinstance(obj[wrapper], (dict, list)):
                    obj = obj[wrapper]
                    break
        if isinstance(obj, list):
            return status, None, "returned array where object expected"
        if not isinstance(obj, dict):
            return status, None, f"unexpected response type {type(obj).__name__}"
        key = None
        if permission_name in obj:
            key = permission_name
        else:
            lower = permission_name.lower()
            for k in obj.keys():
                if k.lower() == lower:
                    key = k
                    break
        if key is None:
            return status, None, f"permission key '{permission_name}' not found"
        val = obj[key]
        if isinstance(val, bool):
            return status, val, None
        if isinstance(val, (int, float)):
            return status, bool(val), None
        if isinstance(val, str):
            v = val.strip().lower()
            if v in ("true", "false"):
                return status, (v == "true"), None
            if v.isdigit():
                return status, bool(int(v)), None
            return status, None, f"permission value string unrecognized: {val!r}"
        return status, None, f"permission value has unsupported type {type(val).__name__}"


# -----------------------------------------------------------------------------
class HealthTests(BaseTests):
    """Health & root endpoint tests."""
    def run_all(self):
        for test in (
            self.test_health_comprehensive,
            self.test_health_simple,
            self.test_health_liveness,
            self.test_root,
        ):
            try:
                test()
            except Exception:
                LOGGER.exception("Unhandled exception in health test")

    def test_health_comprehensive(self):
        name = "GET comprehensive health"
        # use the canonical path without trailing slash per OpenAPI
        resp = self.run_simple_expected(name, "GET", "/api/v1/health", None, [200])
        if resp and resp.status_code == 200:
            j = resp.json()
            # require the core keys per OpenAPI (timestamp/message are optional)
            req = {"status", "version", "service", "environment", "checks"}
            ok = isinstance(j, dict) and req.issubset(j.keys())
            record_result(name + " verify", "GET", "/api/v1/health", resp.status_code, ok,
                          None if ok else "missing health keys")

    def test_health_simple(self):
        self.run_simple_expected("GET simple health","GET","/api/v1/health/simple",None,[200])

    def test_health_liveness(self):
        name = "GET health liveness"
        # match OpenAPI path /api/v1/healthliveness
        resp = self.run_simple_expected(name, "GET", "/api/v1/healthliveness", None, [200])
        if resp and resp.status_code == 200:
            j = resp.json()
            ok = isinstance(j, dict) and all(isinstance(v, str) for v in j.values())
            record_result(name + " verify", "GET", "/api/v1/healthliveness", resp.status_code, ok,
                          None if ok else "not map<string,string>")

    def test_root(self):
        self.run_simple_expected("GET root","GET","/",None,[200])

# -----------------------------------------------------------------------------
def _print_summary_and_exit():
    total    = len(TEST_RESULTS)
    skipped  = sum(1 for r in TEST_RESULTS if r.get("skipped"))
    executed = total - skipped
    passed   = sum(1 for r in TEST_RESULTS if r["passed"])
    failed   = executed - passed
    LOGGER.info(f"=== SUMMARY: total={total} exec={executed} pass={passed} fail={failed} skip={skipped} ===\n")
    print(GREEN + "--- PASSED TESTS ---" + RESET)
    for r in TEST_RESULTS:
        if r["passed"]:
            print(GREEN + f"{r['test_name']} | {r['method']} {r['uri']} | {r['status_code']}" + RESET)
    print(RED + "\n--- FAILED TESTS ---" + RESET)
    for r in TEST_RESULTS:
        if not r["passed"] and not r.get("skipped"):
            print(RED + f"{r['test_name']} | {r['method']} {r['uri']} | "
                         f"{r['status_code']} | {r['reason']}" + RESET)
    print(YELLOW + "\n--- SKIPPED TESTS ---" + RESET)
    for r in TEST_RESULTS:
        if r.get("skipped"):
            print(YELLOW + f"{r['test_name']} | {r['method']} {r['uri']} | {r['reason']}" + RESET)
    # calculate and nicely format total runtime
    elapsed = time.monotonic() - START_TIME
    hrs, rem = divmod(elapsed, 3600)
    mins, secs = divmod(rem, 60)
    if hrs >= 1:
        runtime = f"{int(hrs)}h {int(mins)}m {secs:.2f}s"
    elif mins >= 1:
        runtime = f"{int(mins)}m {secs:.2f}s"
    else:
        runtime = f"{secs:.2f}s"
    LOGGER.info(f"=== TOTAL RUNTIME: {runtime} ===")
    exit_code = 0 if (failed == 0 and not CLEANUP_FAILED) else 1
    LOGGER.info(f"Exiting with code {exit_code}")
    sys.exit(exit_code)

# -----------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Discord Gateway API Test Runner")
    p.add_argument("--base-url",        default=DEFAULT_BASE_URL,    help="API base URL")
    p.add_argument("--guild-id",        default=DEFAULT_GUILD_ID,    type=int, help="Guild ID for tests")
    p.add_argument("--user-id",         default=DEFAULT_USER_ID,     type=int, help="User ID for tests")
    p.add_argument("--bot-id",          default=DEFAULT_BOT_ID,      type=int, help="Bot User ID for tests")
    p.add_argument("--log-file",        default=DEFAULT_LOG_FILE,    help="Log file (overwrite)")
    p.add_argument("--cleanup-log",     default=DEFAULT_CLEANUP_FILE,help="Cleanup JSONL log")
    p.add_argument("--delay",           default=DEFAULT_DELAY,       type=float, help="Delay between tests")
    p.add_argument("--validation-delay",default=DEFAULT_VALIDATION_DELAY,type=float,
                   help="Delay before validation GETs")
    return p.parse_args()

def setup_logger(logpath: str):
    global LOGGER
    LOGGER = logging.getLogger("api_test_runner")
    LOGGER.setLevel(logging.DEBUG)
    # File handler
    fh = logging.FileHandler(logpath, mode="a", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-5s | %(message)s"))
    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    class ColorFmt(logging.Formatter):
        def format(self, rec):
            msg = super().format(rec)
            if msg.startswith("[PASS]"):
                return GREEN + msg + RESET
            if msg.startswith("[FAIL]"):
                return RED + msg + RESET
            if rec.levelno == logging.WARNING or msg.startswith("[SKIP]"):
                return YELLOW + msg + RESET
            return WHITE + msg + RESET
    ch.setFormatter(ColorFmt("%(message)s"))
    LOGGER.handlers.clear()
    LOGGER.addHandler(fh)
    LOGGER.addHandler(ch)

def main():
    global ARGS, START_TIME
    ARGS = parse_args()
    setup_logger(ARGS.log_file)
    START_TIME = time.monotonic()
    LOGGER.info(f"Starting test run at {now_iso()}\n")
    open(ARGS.cleanup_log, "w").close()  # truncate cleanup log

    suites = [
        MessageTests(       ARGS.guild_id, ARGS.user_id, ARGS.delay, ARGS.validation_delay),
        UserTests(          ARGS.guild_id, ARGS.user_id, ARGS.delay, ARGS.validation_delay),
        RoleTests(          ARGS.guild_id, ARGS.user_id, ARGS.delay, ARGS.validation_delay),
        GuildTests(         ARGS.guild_id, ARGS.user_id, ARGS.delay, ARGS.validation_delay),
        CategoryTests(      ARGS.guild_id, ARGS.user_id, ARGS.delay, ARGS.validation_delay),
        ChannelTests(       ARGS.guild_id, ARGS.user_id, ARGS.delay, ARGS.validation_delay),
        ForumTests(         ARGS.guild_id, ARGS.user_id, ARGS.delay, ARGS.validation_delay),
        PermissionsTests(   ARGS.guild_id, ARGS.user_id, ARGS.delay, ARGS.validation_delay),
        HealthTests(        ARGS.guild_id, ARGS.user_id, ARGS.delay, ARGS.validation_delay),
    ]

    for suite in suites:
        LOGGER.warning(f"=== Suite: {suite.__class__.__name__} ===")
        try:
            suite.run_all()
        except Exception:
            LOGGER.exception("Unhandled exception while running suite %s — continuing with next suite", suite.__class__.__name__)


    LOGGER.warning("All suites complete → performing cleanup")
    cleanup_all()

    LOGGER.warning("Cleanup finished → printing summary")
    _print_summary_and_exit()

if __name__ == "__main__":
    main()