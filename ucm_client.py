"""
ucm_client.py - pull call records straight from a Grandstream UCM over its API.

Two documented ways in, tried in this order when mode="auto":

1. HTTPS API (UCM63xx, "Integrations > API Configuration > HTTPS API Settings (New)"):
       POST https://<ucm>:8089/api   {"request": {"action": "challenge", "user": ..., "version": "1.0"}}
       token = md5(challenge + password)
       POST {"request": {"action": "login", "user": ..., "token": ...}}        -> cookie (valid 10 minutes)
       POST {"request": {"action": "cdrapi", "cookie": ..., "format": "json", "startTime": ..., ...}}
2. Legacy CDR API (digest authentication):
       GET  https://<ucm>:8443/cdrapi?format=json&startTime=...&endTime=...&numRecords=1000&offset=N

Both return {"cdr_root": [ {"cdr": session, "main_cdr": {...}, "sub_cdr_1": {...}, ...}, ... ]}.
Records are converted to the same columns as the UCM's CSV export, so the analysis code is shared.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd
import requests
import urllib3

PAGE = 1000  # the UCM's maximum records per request

# UCM API field -> CSV export column
FIELD_MAP = {
    "accountcode": "Account Code", "src": "Caller Number", "dst": "Callee Number", "dcontext": "Context",
    "clid": "Caller ID", "channel": "Source Channel", "dstchannel": "Dest. Channel", "lastapp": "Last app.",
    "lastdata": "Last data", "start": "Start Time", "answer": "Answer Time", "end": "End Time",
    "duration": "Call Time", "billsec": "Talk Time", "disposition": "Call Status", "amaflags": "AMA Flags",
    "uniqueid": "Unique ID", "dstchanext": "Dest Channel Extension", "caller_name": "Caller Name",
    "dstanswer": "Answered by", "session": "Session", "action_owner": "Premier Caller",
    "action_type": "Action Type", "src_trunk_name": "Source Trunk Name", "dst_trunk_name": "Destination Trunk Name",
}


class UCMError(Exception):
    pass


@dataclass
class UCMConfig:
    host: str
    user: str
    password: str
    port: int = 8089
    legacy_port: int = 8443
    scheme: str = "https"
    mode: str = "auto"            # "auto", "https_api" or "legacy"
    verify_ssl: bool = False      # UCMs usually use a self-signed certificate
    timeout: int = 30

    @classmethod
    def from_secrets(cls, d) -> "UCMConfig":
        d = dict(d)
        return cls(host=str(d["host"]).strip().replace("https://", "").replace("http://", "").rstrip("/"),
                   user=str(d["user"]), password=str(d["password"]), port=int(d.get("port", 8089)),
                   legacy_port=int(d.get("legacy_port", 8443)), scheme=str(d.get("scheme", "https")),
                   mode=str(d.get("mode", "auto")), verify_ssl=bool(d.get("verify_ssl", False)),
                   timeout=int(d.get("timeout", 30)))


@dataclass
class FetchResult:
    table: pd.DataFrame
    calls: int
    method: str
    log: list = field(default_factory=list)
    fetched_at: datetime = field(default_factory=datetime.now)


class UCMClient:
    def __init__(self, cfg: UCMConfig):
        self.cfg = cfg
        self.s = requests.Session()
        self.s.verify = cfg.verify_ssl
        if not cfg.verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self.cookie: str | None = None
        self.cookie_at = 0.0
        self.log: list[str] = []

    # ---------------------------------------------------------------- HTTPS API
    @property
    def api_url(self) -> str:
        return f"{self.cfg.scheme}://{self.cfg.host}:{self.cfg.port}/api"

    def _post(self, payload: dict) -> dict:
        try:
            r = self.s.post(self.api_url, json={"request": payload}, timeout=self.cfg.timeout)
        except requests.RequestException as e:
            raise UCMError(f"Couldn't reach the phone system at {self.api_url} ({e.__class__.__name__}).") from e
        if r.status_code != 200:
            raise UCMError(f"Phone system replied HTTP {r.status_code} on {payload.get('action')}.")
        try:
            data = r.json()
        except ValueError as e:
            raise UCMError(f"Unexpected reply to {payload.get('action')} (not JSON).") from e
        return data

    def login(self) -> str:
        ch = self._post({"action": "challenge", "user": self.cfg.user, "version": "1.0"})
        if ch.get("status") != 0:
            raise UCMError(_status_msg("challenge", ch.get("status")))
        challenge = ch["response"]["challenge"]
        token = hashlib.md5(f"{challenge}{self.cfg.password}".encode()).hexdigest()
        lg = self._post({"action": "login", "user": self.cfg.user, "token": token})
        if lg.get("status") != 0:
            raise UCMError(_status_msg("login", lg.get("status")))
        self.cookie = lg["response"]["cookie"]
        self.cookie_at = time.time()
        self.log.append("Logged in to the HTTPS API.")
        return self.cookie

    def logout(self):
        if self.cookie:
            try:
                self._post({"action": "logout", "cookie": self.cookie})
            except UCMError:
                pass
            self.cookie = None

    def _api_page(self, start: str, end: str, offset: int) -> list:
        if not self.cookie or time.time() - self.cookie_at > 8 * 60:  # cookies last 10 minutes
            self.login()
        payload = {"action": "cdrapi", "cookie": self.cookie, "format": "json", "startTime": start,
                   "endTime": end, "numRecords": str(PAGE), "offset": str(offset)}
        data = self._post(payload)
        if isinstance(data, dict) and data.get("status") not in (None, 0) and "cdr_root" not in json.dumps(data)[:200]:
            if data.get("status") == -6:  # cookie expired - log in again once
                self.login()
                payload["cookie"] = self.cookie
                data = self._post(payload)
            if data.get("status") not in (None, 0):
                raise UCMError(_status_msg("cdrapi", data.get("status")))
        return _find_root(data)

    # ---------------------------------------------------------------- legacy CDR API
    def _legacy_page(self, start: str, end: str, offset: int) -> list:
        url = f"{self.cfg.scheme}://{self.cfg.host}:{self.cfg.legacy_port}/cdrapi"
        params = {"format": "json", "startTime": start, "endTime": end, "numRecords": PAGE, "offset": offset}
        try:
            r = self.s.get(url, params=params, timeout=self.cfg.timeout,
                           auth=requests.auth.HTTPDigestAuth(self.cfg.user, self.cfg.password))
        except requests.RequestException as e:
            raise UCMError(f"Couldn't reach the legacy CDR API at {url} ({e.__class__.__name__}).") from e
        if r.status_code == 401:
            raise UCMError("Legacy CDR API rejected the username/password.")
        if r.status_code != 200:
            raise UCMError(f"Legacy CDR API replied HTTP {r.status_code}.")
        try:
            return _find_root(r.json())
        except ValueError as e:
            raise UCMError("Legacy CDR API reply was not JSON.") from e

    # ---------------------------------------------------------------- public
    def fetch(self, start: datetime, end: datetime) -> FetchResult:
        """All calls that started between start and end (inclusive)."""
        s_txt, e_txt = start.strftime("%Y-%m-%dT%H:%M:%S"), end.strftime("%Y-%m-%dT%H:%M:%S")
        methods = {"auto": ["https_api", "legacy"], "https_api": ["https_api"], "legacy": ["legacy"]}[self.cfg.mode]
        errors = []
        for method in methods:
            pager = self._api_page if method == "https_api" else self._legacy_page
            try:
                records, offset = [], 0
                while True:
                    page = pager(s_txt, e_txt, offset)
                    records.extend(page)
                    self.log.append(f"{method}: fetched {len(page)} calls (offset {offset}).")
                    if len(page) < PAGE:
                        break
                    offset += PAGE
                    if offset > 500_000:
                        raise UCMError("Too many records - choose a shorter period.")
                if method == "https_api":
                    self.logout()
                return FetchResult(records_to_table(records), len(records), method, self.log)
            except UCMError as e:
                errors.append(f"{'HTTPS API' if method == 'https_api' else 'Legacy CDR API'}: {e}")
                self.log.append(str(e))
        raise UCMError(" | ".join(errors))

    def test(self, now: datetime | None = None) -> list[tuple[str, bool, str]]:
        """Step-by-step connection check for the admin panel (now = the UCM's local time)."""
        steps = []
        now = now or datetime.now()
        if self.cfg.mode in ("auto", "https_api"):
            try:
                self.login()
                steps.append(("HTTPS API login", True, f"Logged in as '{self.cfg.user}'"))
                page = self._api_page(now.replace(hour=0, minute=0, second=0).strftime("%Y-%m-%dT%H:%M:%S"),
                                      now.strftime("%Y-%m-%dT%H:%M:%S"), 0)
                steps.append(("Read today's call records", True, f"{len(page)} calls so far today"))
                self.logout()
                return steps
            except UCMError as e:
                steps.append(("HTTPS API", False, str(e)))
        if self.cfg.mode in ("auto", "legacy"):
            try:
                page = self._legacy_page(now.replace(hour=0, minute=0, second=0).strftime("%Y-%m-%dT%H:%M:%S"),
                                         now.strftime("%Y-%m-%dT%H:%M:%S"), 0)
                steps.append(("Legacy CDR API", True, f"{len(page)} calls so far today"))
            except UCMError as e:
                steps.append(("Legacy CDR API", False, str(e)))
        return steps


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
STATUS = {-1: "invalid parameters", -5: "authentication required", -6: "session expired or missing cookie",
          -7: "connection closed", -8: "timed out", -15: "invalid value", -37: "wrong username or password"}


def _status_msg(action: str, status) -> str:
    return (f"{action} failed: {STATUS.get(status, f'error code {status}')}. If this persists, check the API user's "
            f"password and that this server's IP is on its Address Allowlist.")


def _find_root(data) -> list:
    """Locate the cdr_root list wherever the UCM nests it."""
    if isinstance(data, dict):
        if "cdr_root" in data:
            return data["cdr_root"] or []
        for v in data.values():
            found = _find_root(v)
            if found is not None:
                return found
        return [] if data.get("status") == 0 else None
    if isinstance(data, list):
        return data
    return None


def records_to_table(records: list) -> pd.DataFrame:
    """cdr_root records -> rows in the UCM CSV export layout (one main_cdr + its sub_cdrs per call)."""
    rows = []
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        main = rec.get("main_cdr") or {}
        session = rec.get("cdr") or main.get("session", "")
        if main.get("src_trunk_name"):
            ctype = "Inbound"
        elif main.get("dst_trunk_name"):
            ctype = "Outbound"
        else:
            ctype = "Internal"
        subs = sorted([k for k in rec if k.startswith("sub_cdr")],
                      key=lambda k: int("".join(ch for ch in k if ch.isdigit()) or 0))
        for kind, part in [("main_cdr", main)] + [("sub_cdr", rec[k]) for k in subs]:
            if not isinstance(part, dict) or not part:
                continue
            row = {"CDR": kind, "Call Type": ctype}
            for k, col in FIELD_MAP.items():
                row[col] = "" if part.get(k) is None else str(part.get(k))
            row["Session"] = row["Session"] or session
            if not row["Dest Channel Extension"]:
                row["Dest Channel Extension"] = str(part.get("dstchannel_ext") or "")
            rows.append(row)
    cols = ["CDR", "Call Type"] + list(FIELD_MAP.values())
    return pd.DataFrame(rows, columns=cols)
