#!/usr/bin/env python3
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from tiktok_checker import CheckerError, is_url, process_source

logger = logging.getLogger("fps-api")

API_HOST = os.getenv("FPS_API_HOST", "0.0.0.0").strip() or "0.0.0.0"
API_PORT = int(os.getenv("FPS_API_PORT", "3008"))
API_KEY = os.getenv("FPS_API_KEY", "").strip()
MAX_JOBS = max(1, int(os.getenv("FPS_API_MAX_JOBS", "2")))
JOB_TTL_SECONDS = max(300, int(os.getenv("FPS_API_JOB_TTL_SECONDS", "1800")))

_executor = ThreadPoolExecutor(max_workers=MAX_JOBS, thread_name_prefix="fps-api")
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _now() -> float:
    return time.time()


def _cleanup_jobs() -> None:
    cutoff = _now() - JOB_TTL_SECONDS
    with _jobs_lock:
        stale = [job_id for job_id, item in _jobs.items() if item.get("updated_at", 0) < cutoff]
        for job_id in stale:
            _jobs.pop(job_id, None)


def _serialize_report(report) -> dict:
    data = asdict(report)
    data["resolution"] = report.resolution
    data["fps"] = round(float(report.fps or 0), 3)
    data["video_bitrate_kbps"] = round(float(report.video_bitrate_kbps or 0), 2)
    data["overall_bitrate_kbps"] = round(float(report.overall_bitrate_kbps or 0), 2)
    data["duration_seconds"] = round(float(report.duration_seconds or 0), 3)
    data["file_size_mb"] = round(float(report.file_size_mb or 0), 3)
    return data


def _run_job(job_id: str, url: str) -> None:
    with _jobs_lock:
        item = _jobs.get(job_id)
        if item:
            item.update(status="processing", updated_at=_now())

    try:
        report = process_source(url)
        result = _serialize_report(report)
        with _jobs_lock:
            item = _jobs.get(job_id)
            if item:
                item.update(status="done", result=result, updated_at=_now())
    except CheckerError as exc:
        with _jobs_lock:
            item = _jobs.get(job_id)
            if item:
                item.update(status="error", error=str(exc), updated_at=_now())
    except Exception as exc:  # pragma: no cover - defensive server boundary
        logger.exception("FPS API job failed")
        with _jobs_lock:
            item = _jobs.get(job_id)
            if item:
                item.update(status="error", error="Internal checker error", updated_at=_now())


class FPSAPIHandler(BaseHTTPRequestHandler):
    server_version = "TheziessFPSAPI/1.0"

    def log_message(self, fmt: str, *args) -> None:
        logger.info("%s - %s", self.address_string(), fmt % args)

    def _authorized(self) -> bool:
        if not API_KEY:
            return True
        auth = self.headers.get("Authorization", "")
        return auth == f"Bearer {API_KEY}"

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 64 * 1024:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}

    def do_OPTIONS(self) -> None:
        self._json(HTTPStatus.NO_CONTENT, {})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            return self._json(HTTPStatus.OK, {
                "ok": True,
                "service": "theziess-fps-api",
                "status": "ready",
                "port": API_PORT,
            })

        if not self._authorized():
            return self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "Unauthorized"})

        if parsed.path == "/api/check-video/status":
            _cleanup_jobs()
            job_id = (parse_qs(parsed.query).get("job_id") or [""])[0]
            if not job_id:
                return self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "job_id is required"})
            with _jobs_lock:
                item = dict(_jobs.get(job_id) or {})
            if not item:
                return self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Job not found"})
            payload = {"ok": True, "jobId": job_id, "status": item.get("status")}
            if item.get("status") == "done":
                payload["result"] = item.get("result")
            elif item.get("status") == "error":
                payload["error"] = item.get("error") or "Checker failed"
            return self._json(HTTPStatus.OK, payload)

        return self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if not self._authorized():
            return self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "Unauthorized"})

        if parsed.path == "/api/check-video/start":
            _cleanup_jobs()
            body = self._read_json()
            url = str(body.get("url") or "").strip()
            if not url or not is_url(url):
                return self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Valid video URL is required"})
            job_id = uuid.uuid4().hex
            with _jobs_lock:
                _jobs[job_id] = {
                    "status": "queued",
                    "url": url,
                    "created_at": _now(),
                    "updated_at": _now(),
                }
            _executor.submit(_run_job, job_id, url)
            return self._json(HTTPStatus.ACCEPTED, {
                "ok": True,
                "jobId": job_id,
                "status": "queued",
            })

        # Optional synchronous endpoint for manual testing/curl.
        if parsed.path == "/api/check-video":
            body = self._read_json()
            url = str(body.get("url") or "").strip()
            if not url or not is_url(url):
                return self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Valid video URL is required"})
            try:
                report = process_source(url)
                return self._json(HTTPStatus.OK, {"ok": True, "result": _serialize_report(report)})
            except CheckerError as exc:
                return self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"ok": False, "error": str(exc)})
            except Exception:
                logger.exception("Synchronous FPS API request failed")
                return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "Internal checker error"})

        return self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})


def start_fps_api_server() -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((API_HOST, API_PORT), FPSAPIHandler)
    thread = threading.Thread(target=server.serve_forever, name="fps-api-server", daemon=True)
    thread.start()
    logger.info("FPS API listening on http://%s:%s", API_HOST, API_PORT)
    return server
