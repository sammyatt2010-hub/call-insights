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

CLIENT_VERSION = "2026-10-13c"  # shown in errors and the connection test, to confirm which file is deployed
PAGE = 1000  # the UCM's maximum records per request
_LEARNED: dict = {}  # (host, user) -> {"version": ..., "hash": ...} that worked, kept while the app runs
_HOLD: dict = {}     # (host, user) -> (until_epoch, reason): don't send logins until then
FAIL_BACKOFF = 15 * 60  # after a refused login, wait this long before the portal tries again by itself


def _hold_msg(key) -> str | None:
    until, reason = _HOLD.get(key, (0, ""))
    left = until - time.time()
    if left <= 0:
        return None
    return f"{reason} The portal won't try again for {int(left // 60) + 1} min, to avoid locking the account."

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
    token_hash: str = "auto"      # auto | md5 | sha256
    timeout: int = 30

    @classmethod
    def from_secrets(cls, d) -> "UCMConfig":
        d = dict(d)
        return cls(host=str(d["host"]).strip().replace("https://", "").replace("http://", "").rstrip("/"),
                   user=str(d["user"]).strip(), password=str(d["password"]), port=int(d.get("port", 8089)),
                   legacy_port=int(d.get("legacy_port", 8443)), scheme=str(d.get("scheme", "https")),
                   mode=str(d.get("mode", "auto")), verify_ssl=bool(d.get("verify_ssl", False)),
                   timeout=int(d.get("timeout", 30)), token_hash=str(d.get("token_hash", "auto")))


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
        # match Grandstream's documented example request exactly
        self.s.headers.update({"Content-Type": "application/json;charset=UTF-8", "Connection": "close"})
        if not cfg.verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self.cookie: str | None = None
        learned = _LEARNED.get((cfg.host, cfg.user), {})
        self._version = learned.get("version", None)  # API version the phone system accepted
        self._hash = learned.get("hash")
        self.details: dict = {}
        self.base: str | None = None   # the scheme://host:port that answered
        self.cookie_at = 0.0
        self.log: list[str] = []

    # ---------------------------------------------------------------- HTTPS API
    @property
    def api_url(self) -> str:
        return f"{self.base or self._candidates()[0]}/api"

    def _candidates(self) -> list[str]:
        """Where the HTTPS API might live: the configured port first, then the usual UCM web ports.
        (The API is served on the same port as the UCM's web portal.)"""
        out = []
        for scheme, port in [(self.cfg.scheme, self.cfg.port), ("https", 443), ("https", 8089)]:
            if port == 80:
                scheme = "http"
            b = f"{scheme}://{self.cfg.host}" + ("" if (scheme, port) in (("https", 443), ("http", 80)) else f":{port}")
            if b not in out:
                out.append(b)
        return out

    def _post(self, payload: dict, base: str | None = None) -> dict:
        url = f"{base or self.base or self._candidates()[0]}/api"
        try:
            r = self.s.post(url, data=json.dumps({"request": payload}), timeout=self.cfg.timeout)
        except requests.exceptions.SSLError as e:
            raise UCMError(f"{url}: secure connection failed (SSLError) - this port probably isn't the HTTPS web "
                           f"portal port.") from e
        except requests.RequestException as e:
            raise UCMError(f"{url}: no answer ({e.__class__.__name__}) - port closed or not forwarded.") from e
        if r.status_code != 200:
            raise UCMError(f"{url}: replied HTTP {r.status_code} on {payload.get('action')}.")
        try:
            data = r.json()
        except ValueError as e:
            raise UCMError(f"{url}: reply to {payload.get('action')} wasn't JSON (starts: "
                           f"{_snippet(r.text)}) - is the HTTPS API enabled?") from e
        return data

    def _find_base(self) -> str:
        """Try each candidate address until one answers the challenge request."""
        errors = []
        for base in ([self.base] if self.base else self._candidates()):
            try:
                ch = self._post({"action": "challenge", "user": self.cfg.user, "version": "1.0"}, base)
                self.base = base
                self.log.append(f"HTTPS API found at {base}/api")
                return ch
            except UCMError as e:
                errors.append(str(e))
        raise UCMError(" / ".join(errors))

    def _challenge(self, version):
        req = {"action": "challenge", "user": self.cfg.user}
        if version:
            req["version"] = version
        return self._post(req)

    def login(self, force: bool = False) -> str:
        key = (self.cfg.host, self.cfg.user)
        held = _hold_msg(key)
        if held and (not force or _HOLD[key][1].startswith("The phone system has locked")):
            raise UCMError(held)
        # 1) challenge - newer firmware (e.g. GCC6000 series) may reject version 1.0; the field is optional
        if self._version is not None and self.base is None:
            self._find_base()  # locate the API address (the reply to this probe isn't used)
            ch = self._challenge(self._version)
        else:
            ch = self._find_base()
        tried = ["1.0"]
        version = "1.0" if self._version is None else self._version
        for v in (None, "1.2", "2.0"):
            if ch.get("status") != -1 or self._version is not None:
                break
            self.log.append(f"challenge (version {tried[-1]}) rejected: {_snippet(json.dumps(ch), 200)}")
            tried.append(v or "none")
            version = v
            ch = self._challenge(v)
        if ch.get("status") != 0:
            if ch.get("status") == -1:
                raise UCMError(f"the phone system answered 'invalid parameters' to the login challenge for API user "
                               f"'{self.cfg.user}' (tried API versions {', '.join(tried)}; last reply "
                               f"{_snippet(json.dumps(ch), 200)}). Check the username exactly matches the API user "
                               f"(it's case-sensitive, letters and numbers only) and that the HTTPS API is enabled.")
            raise UCMError(_status_msg("challenge", ch.get("status")))
        self._version = version

        # 2) login - token is md5(challenge + password); some newer firmware may use sha256.
        #    At most two attempts, so the address isn't locked out for repeated failures.
        if self.cfg.token_hash in ("md5", "sha256"):
            hashes = [self.cfg.token_hash]
        elif self._hash:
            hashes = [self._hash]
        else:
            hashes = ["md5"]  # Grandstream documents token = MD5(challenge + password); one attempt per login
        last = None
        for i, h in enumerate(hashes):
            if i:  # every login needs a fresh challenge
                ch = self._challenge(version)
                if ch.get("status") != 0:
                    break
            challenge = ch["response"]["challenge"]
            fn = hashlib.md5 if h == "md5" else hashlib.sha256
            token = fn(f"{challenge}{self.cfg.password}".encode()).hexdigest()
            lg = self._post({"action": "login", "user": self.cfg.user, "token": token})
            last = lg
            if lg.get("status") == 0:
                self.cookie = lg["response"]["cookie"]
                self.cookie_at = time.time()
                self.details = {"version": version or "none", "hash": h, "base": self.base}
                self._hash = h
                _LEARNED[(self.cfg.host, self.cfg.user)] = {"version": version, "hash": h}
                self.log.append(f"Logged in to the HTTPS API (API version {version or 'none'}, {h} token).")
                return self.cookie
            self.log.append(f"login with {h} token refused: {_snippet(json.dumps(lg), 200)}")
            if lg.get("status") != -37:
                break  # locked out or another error - never keep trying
        status = (last or {}).get("status")
        msg = _status_msg("login", status)
        if status == -68:
            remain = int(((last or {}).get("response") or {}).get("remain_time") or 900)
            _HOLD[key] = (time.time() + remain + 30,
                          "The phone system has locked API logins after too many failed attempts.")
            raise UCMError(_hold_msg(key) + " Please don't press refresh or test until then.")
        if status == -37:
            _HOLD[key] = (time.time() + FAIL_BACKOFF, "The last login was refused (wrong username or password).")
        if status == -37:
            left = ((last or {}).get("response") or {}).get("remain_num")
            msg = (f"login failed: the phone system says the username or password is wrong for API user "
                   f"'{self.cfg.user}'" + (f" ({left} attempt(s) left before it bans this address)" if left else "")
                   + ". The login format matches Grandstream's documentation, so the password in Secrets doesn't "
                   "match the API user's password on the phone system - re-set it on both sides.")
        raise UCMError(msg)

    def logout(self):
        if self.cookie:
            try:
                self._post({"action": "logout", "cookie": self.cookie})
            except UCMError:
                pass
            self.cookie = None

    def _api_page(self, start: str, end: str, offset: int) -> list:
        if not self.cookie or time.time() - self.cookie_at > 4 * 60:  # sessions expire after 5 idle minutes
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
            raise UCMError(f"Legacy CDR API reply wasn't JSON (starts: {_snippet(r.text)}).") from e

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
        raise UCMError(" | ".join(errors) + f" [client {CLIENT_VERSION}]")

    def diagnose(self) -> list[tuple[str, bool, str]]:
        """Challenge-only probes (no login attempts, so nothing counts towards a lock-out).
        Shows whether the phone system recognises the API username at all."""
        rows = [("Connector version", True, f"ucm_client.py {CLIENT_VERSION}"),
                ("API user in Secrets", True, f"'{self.cfg.user}' · password {password_fingerprint(self.cfg.password)}")]
        try:
            self._find_base()
        except UCMError as e:
            return rows + [("Reach the API", False, str(e))]
        rows.append(("Reach the API", True, f"{self.base}/api answered"))
        probes = [("Your API user, version 1.0", self.cfg.user, "1.0"),
                  ("Your API user, no version", self.cfg.user, None),
                  ("Made-up user 'zz9notauser', version 1.0", "zz9notauser", "1.0"),
                  ("Made-up user 'zz9notauser', no version", "zz9notauser", None)]
        for label, user, ver in probes:
            req = {"action": "challenge", "user": user}
            if ver:
                req["version"] = ver
            try:
                rep = self._post(req)
                rows.append((label, rep.get("status") == 0, _snippet(json.dumps(rep), 160)))
            except UCMError as e:
                rows.append((label, False, str(e)))
        r = {lbl: ok for lbl, ok, _ in rows}
        real1, fake1 = r.get(probes[0][0]), r.get(probes[2][0])
        real0, fake0 = r.get(probes[1][0]), r.get(probes[3][0])
        if real1:
            verdict = "The API username is recognised with version 1.0."
        elif fake0 and real0 and not fake1:
            verdict = ("Version 1.0 is refused for any user and the no-version challenge is given to anyone, so this "
                       "can't confirm the username - the password check is where it fails.")
        elif real0 and fake0:
            verdict = ("The phone system hands out a challenge to any username, so a wrong username and a wrong "
                       "password look the same here. Double-check both on the API user.")
        else:
            verdict = "Unusual pattern - please send these results to SY Communications."
        rows.append(("What this means", True, verdict))
        return rows

    def try_login_variants(self) -> list[tuple[str, bool, str]]:
        """Two alternative login formats (each failure can count towards a lock-out)."""
        rows = []
        held = _hold_msg((self.cfg.host, self.cfg.user))
        if held and _HOLD[(self.cfg.host, self.cfg.user)][1].startswith("The phone system has locked"):
            return [("Locked", False, held)]
        try:
            self._find_base()
        except UCMError as e:
            return [("Reach the API", False, str(e))]
        variants = [
            ("md5(challenge+password) + url field", lambda c: hashlib.md5(f"{c}{self.cfg.password}".encode()).hexdigest(),
             {"url": f"{self.base}"}),
            ("md5(password+challenge)", lambda c: hashlib.md5(f"{self.cfg.password}{c}".encode()).hexdigest(), {}),
        ]
        for label, tok, extra in variants:
            ch = self._challenge(self._version if self._version is not None else None)
            if ch.get("status") != 0:
                rows.append((label, False, f"challenge refused: {_snippet(json.dumps(ch), 120)}"))
                continue
            payload = {"action": "login", "user": self.cfg.user, "token": tok(ch["response"]["challenge"]), **extra}
            lg = self._post(payload)
            ok = lg.get("status") == 0
            rows.append((label, ok, _snippet(json.dumps(lg), 140)))
            if lg.get("status") == -68:
                remain = int((lg.get("response") or {}).get("remain_time") or 900)
                _HOLD[(self.cfg.host, self.cfg.user)] = (time.time() + remain + 30,
                                                          "The phone system has locked API logins after too many "
                                                          "failed attempts.")
                rows.append(("Locked", False, f"Stopped - locked for about {remain // 60 + 1} min."))
                return rows
            if ok:
                self.cookie = lg["response"]["cookie"]
                self.logout()
                rows.append(("Result", True, f"This format works - tell SY Communications: '{label}'."))
                return rows
        rows.append(("Result", False, "None of the alternative formats were accepted, so the username/password "
                                      "on the phone system most likely don't match Secrets."))
        return rows

    def test(self, now: datetime | None = None) -> list[tuple[str, bool, str]]:
        """Step-by-step connection check for the admin panel (now = the UCM's local time)."""
        steps = [("Connector version", True, f"ucm_client.py {CLIENT_VERSION}"),
                 ("API user in Secrets", True,
                  f"'{self.cfg.user}' · password {password_fingerprint(self.cfg.password)}")]
        now = now or datetime.now()
        if self.cfg.mode in ("auto", "https_api"):
            try:
                self.login(force=True)
                steps.append(("HTTPS API login", True,
                              f"Logged in as '{self.cfg.user}' at {self.base}/api (API version "
                              f"{self.details.get('version')}, {self.details.get('hash')} token)"))
                page = self._api_page(now.replace(hour=0, minute=0, second=0).strftime("%Y-%m-%dT%H:%M:%S"),
                                      now.strftime("%Y-%m-%dT%H:%M:%S"), 0)
                steps.append(("Read today's call records", True, f"{len(page)} calls so far today"))
                self.logout()
                return steps
            except UCMError as e:
                steps.append(("HTTPS API", False, str(e)))
                if self.log:
                    steps.append(("What the phone system said", False, " · ".join(self.log[-4:])))
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
STATUS = {-68: "logins temporarily locked after too many failed attempts", -1: "invalid parameters", -5: "authentication required", -6: "session expired or missing cookie",
          -7: "connection closed", -8: "timed out", -15: "invalid value", -37: "wrong username or password"}


def _snippet(text: str, n: int = 80) -> str:
    t = " ".join(str(text).split())
    return f'"{t[:n]}{"..." if len(t) > n else ""}"' if t else "empty"


def password_fingerprint(pw: str) -> str:
    """Enough to compare with what's set on the phone system, without revealing the password."""
    pw = str(pw)
    if not pw:
        return "EMPTY - no password in Secrets"
    flags = []
    if pw != pw.strip():
        flags.append("has spaces at the start or end")
    if any(c in pw for c in "\\\"'"):
        flags.append("contains a quote or backslash")
    if not pw.isascii():
        flags.append("contains non-standard characters (e.g. a pasted curly quote)")
    shown = f"{pw[0]}{'•' * max(0, len(pw) - 2)}{pw[-1]}" if len(pw) > 2 else "•" * len(pw)
    return f"{len(pw)} characters, {shown}" + (f" - WARNING: {', '.join(flags)}" if flags else "")


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
