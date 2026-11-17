"""Bounded, read-only transport and output contract for optional provider skills."""

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime

CONTRACT_VERSION = 1
VN = timezone(timedelta(hours=7))


class ReadError(Exception):
    """A safe code, never a provider response or a credential-bearing URL."""

    def __init__(self, code, http_status=None):
        super().__init__(code)
        self.code = code
        self.http_status = http_status

    def as_dict(self):
        return {"code": self.code, "http_status": self.http_status}


def configuration(name):
    """Honor native profile scope in process; use the scoped child env in a CLI."""
    try:
        from agent.secret_scope import current_secret_scope, is_multiplex_active
    except ImportError:
        return os.environ.get(name, "").strip()
    scope = current_secret_scope()
    if scope is not None:
        return str(scope.get(name, "")).strip()
    if is_multiplex_active():
        raise ReadError("missing_profile_scope")
    return os.environ.get(name, "").strip()


def identifier(value):
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ReadError("invalid_identifier")
    return value


def day_interval(start=None, end=None, today=False, days=None, now=None):
    now = now or datetime.now(VN)
    if today and (start or end or days is not None):
        raise ReadError("conflicting_dates")
    if days is not None and (start or end or days < 1 or days > 366):
        raise ReadError("invalid_days")
    if today or days is not None:
        end = now.astimezone(VN).date().isoformat()
        start = (now.astimezone(VN).date() - timedelta(days=(days or 1) - 1)).isoformat()
    if not start or not end:
        raise ReadError("date_range_required")
    try:
        low = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=VN)
        high = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=VN)
    except ValueError:
        raise ReadError("invalid_date") from None
    if high < low:
        raise ReadError("invalid_date_range")
    return low.isoformat(), (high + timedelta(days=1, seconds=-1)).isoformat()


def project(record, fields):
    if not isinstance(record, dict):
        raise ReadError("invalid_response")
    result = {key: record[key] for key in fields if key in record}
    for key, value in result.items():
        if isinstance(value, (dict, list)):
            raise ReadError("invalid_response")
        if value is not None and (key == "id" or key.endswith("_id") or key in {"xid", "tid"}):
            result[key] = str(value)
    return result


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ReadError("redirect_rejected", code)


class Reader:
    def __init__(self, base, headers, *, timeout=15, deadline=90, interval=0.5):
        if not 1 <= timeout <= 30 or not 1 <= deadline <= 300:
            raise ReadError("invalid_budget")
        self.base = base
        self.headers = headers
        self.timeout = timeout
        self.end = time.monotonic() + deadline
        self.interval = interval
        self.last = 0.0
        self.calls = 0
        self.complete = True
        self.issues = []
        self.error = None
        # Do not inherit arbitrary proxy destinations from the agent environment.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def pause(self, seconds):
        if time.monotonic() + seconds >= self.end:
            raise ReadError("deadline_exceeded")
        time.sleep(max(0, seconds))

    def get(self, path, params=None):
        if not re.fullmatch(r"/[A-Za-z0-9_./-]+", path) or ".." in path:
            raise ReadError("invalid_path")
        query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
        url = self.base + path + ("?" + query if query else "")
        for attempt in range(3):
            self.pause(max(0, self.interval - (time.monotonic() - self.last)))
            request = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "XNOBrain-Provider-Reader/0.1",
                    **self.headers,
                },
                method="GET",
            )
            self.last = time.monotonic()
            self.calls += 1
            try:
                with self.opener.open(
                    request, timeout=min(self.timeout, self.end - self.last)
                ) as response:
                    raw = response.read(4_000_001)
                if len(raw) > 4_000_000:
                    raise ReadError("response_too_large")
                payload = json.loads(raw, parse_float=Decimal)
                if not isinstance(payload, dict):
                    raise ReadError("invalid_response")
                return payload
            except urllib.error.HTTPError as exc:
                status = exc.code
                retry_after = exc.headers.get("Retry-After", "") if exc.headers else ""
                exc.close()
                if status not in {429, 500, 502, 503, 504} or attempt == 2:
                    code = {
                        401: "authentication_failed",
                        403: "forbidden",
                        404: "not_found",
                        422: "unsupported_or_invalid",
                        429: "rate_limited",
                    }.get(status, "http_error")
                    raise ReadError(code, status) from None
                delay = 2**attempt
                if retry_after:
                    try:
                        delay = max(delay, float(retry_after))
                    except ValueError:
                        try:
                            delay = max(
                                delay,
                                (
                                    parsedate_to_datetime(retry_after) - datetime.now(UTC)
                                ).total_seconds(),
                            )
                        except (ValueError, TypeError, OverflowError):
                            pass
                self.pause(delay)
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt == 2:
                    raise ReadError("network_unavailable") from None
                self.pause(2**attempt)
            except (ValueError, UnicodeError):
                raise ReadError("invalid_response") from None
        raise ReadError("retry_exhausted")

    def partial(self, code):
        self.complete = False
        if code not in self.issues:
            self.issues.append(code)

    def pages(self, path, params, *, key, max_pages, page_size, sepay=False):
        output, seen = [], set()
        for page in range(1, max_pages + 1):
            try:
                payload = self.get(
                    path, {**params, "page": page, "per_page" if sepay else "limit": page_size}
                )
                if sepay and payload.get("status") != "success":
                    raise ReadError("provider_error")
                items = payload.get(key)
                if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                    raise ReadError("invalid_response")
                fresh = 0
                for item in items:
                    identity = str(
                        item.get("id")
                        or item.get("xid")
                        or json.dumps(item, sort_keys=True, default=str)
                    )
                    if identity not in seen:
                        output.append(item)
                        seen.add(identity)
                        fresh += 1
                    else:
                        self.partial("duplicate_records")
                meta = payload.get("meta") or {}
                if not isinstance(meta, dict):
                    raise ReadError("invalid_response")
                pagination = (meta.get("pagination") or {}) if sepay else {}
                if not isinstance(pagination, dict):
                    raise ReadError("invalid_response")
                more = pagination.get("has_more")
                if more is not None and not isinstance(more, bool):
                    raise ReadError("invalid_response")
                if more is None:
                    more = len(items) >= page_size
                if not more:
                    return output
                if not items or not fresh:
                    self.partial("pagination_did_not_advance")
                    return output
            except ReadError as exc:
                if not output:
                    raise
                self.partial(exc.code)
                self.error = exc.as_dict()
                return output
        self.partial("page_budget_exceeded")
        return output


def options(parser):
    parser.add_argument(
        "--json", action="store_true", help="Emit the stable JSON envelope (also the default)."
    )
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--deadline", type=int, default=90)
    parser.add_argument(
        "--max-pages", type=int, choices=range(1, 101), default=20, metavar="1..100"
    )


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        raise ReadError("invalid_arguments")


def result(provider, environment, data, scope, reader=None, error=None, limitations=()):
    complete = error is None and (reader is None or reader.complete)
    return {
        "contract_version": CONTRACT_VERSION,
        "status": "error" if error else ("success" if complete else "partial"),
        "provider": provider,
        "environment": environment,
        "data": data,
        "coverage": {
            "scope": scope,
            "retrieved_at": datetime.now(UTC).isoformat(),
            "record_count": len(data),
            "complete": complete,
            "requests": reader.calls if reader else 0,
            "limitations": [*limitations, *(reader.issues if reader else [])],
        },
        "error": error.as_dict() if error else (reader.error if reader else None),
    }


def emit(payload):
    print(json.dumps(payload, ensure_ascii=False, default=str))
    return {"success": 0, "error": 1, "partial": 3}[payload["status"]]
