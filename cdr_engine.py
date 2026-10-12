"""
cdr_engine.py - turns a raw PBX CDR export into a "true" picture of call handling.

Key idea: the PBX writes one row per *leg* (ring group, overflow group, IVR, voicemail),
so a call that rang the first group, timed out and was then answered by the overflow
group shows up as a "Not Answered" row PLUS an "Answered" row. We stitch legs back
together into single customer calls before counting anything.
"""
from __future__ import annotations

import io
import re
import warnings
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
STD_COLS = ["src", "dst", "start", "duration", "talk", "cost", "status", "uid", "clid", "mos"]

HEADER_ALIASES = {
    "src": ["from", "source", "src", "caller", "calling"],
    "dst": ["to", "destination", "dst", "called", "dialled", "dialed"],
    "start": ["start", "date", "time", "calldate", "call date", "started"],
    "duration": ["duration", "total"],
    "talk": ["talk", "billsec", "billable", "answered time", "talk time"],
    "cost": ["cost", "price", "charge"],
    "status": ["status", "disposition", "result"],
    "uid": ["uniqueid", "unique id", "linkedid", "call id", "callid", "uid"],
    "clid": ["callerid", "caller id", "clid", "cli"],
    "mos": ["mos", "quality"],
}


def _decode(raw: bytes) -> str:
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8")
    # UTF-16 without BOM: lots of NUL bytes
    if raw[:200].count(b"\x00") > 40:
        return raw.decode("utf-16-le")
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _looks_like_header(first_row: list[str]) -> bool:
    joined = " ".join(str(x).lower() for x in first_row)
    return not re.search(r"\d{1,2}\s\w{3}\s\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}", joined)


def load_cdr(raw: bytes) -> pd.DataFrame:
    """Read a CDR export (tab or comma separated, UTF-16/UTF-8, with or without header)."""
    text = _decode(raw)
    first = text.splitlines()[0] if text else ""
    sep = "\t" if first.count("\t") >= first.count(",") else ","
    df = pd.read_csv(io.StringIO(text), sep=sep, header=None, dtype=str, keep_default_na=False)
    df = df.apply(lambda c: c.str.strip())

    if _looks_like_header(df.iloc[0].tolist()):
        header = [str(h).strip().lower() for h in df.iloc[0]]
        df = df.iloc[1:].reset_index(drop=True)
        mapping = {}
        for std, aliases in HEADER_ALIASES.items():
            for i, h in enumerate(header):
                if i not in mapping.values() and any(a == h or a in h for a in aliases):
                    mapping[std] = i
                    break
        out = pd.DataFrame({std: df.iloc[:, i] if i is not None else "" for std, i in
                            ((s, mapping.get(s)) for s in STD_COLS)})
    else:
        cols = STD_COLS[: df.shape[1]] + [f"extra{i}" for i in range(max(0, df.shape[1] - len(STD_COLS)))]
        df.columns = cols
        for c in STD_COLS:
            if c not in df:
                df[c] = ""
        out = df[STD_COLS].copy()

    out["ts"] = pd.to_datetime(out["start"], format="%d %b %Y %H:%M:%S", errors="coerce")
    if out["ts"].isna().mean() > 0.5:
        out["ts"] = pd.to_datetime(out["start"], dayfirst=True, errors="coerce")
    out = out.dropna(subset=["ts"]).copy()
    out["duration_s"] = out["duration"].map(_to_seconds)
    out["talk_s"] = out["talk"].map(_to_seconds)
    out["mos_v"] = pd.to_numeric(out["mos"], errors="coerce")
    out["cost_v"] = pd.to_numeric(out["cost"], errors="coerce").fillna(0.0)
    out["status"] = out["status"].str.strip().str.title()
    if not out["uid"].astype(bool).any():
        out["uid"] = out.index.astype(str)
    return out.reset_index(drop=True)


PROVIDERS = {
    "bicom": "Bicom",
    "trusip": "TruSIP",
    "grandstream": "Grandstream UCM",
}

# What each provider's export can tell us. TruSIP logs one row per call at trunk level,
# so there are no ring groups, overflow legs, extensions, ring times or MOS scores.
CAPS = {
    "bicom": {"routing": True, "ring_time": True},
    "trusip": {"routing": False, "ring_time": False},
    "grandstream": {"routing": True, "ring_time": True},
}


def expand_archives(blobs: list[bytes]) -> list[bytes]:
    """Unpack .tar / .zip / .gz uploads (Grandstream exports arrive as a tar of monthly CSVs)."""
    import gzip
    import tarfile
    import zipfile

    out: list[bytes] = []
    for b in blobs:
        if b[:2] == b"\x1f\x8b":
            try:
                out.extend(expand_archives([gzip.decompress(b)]))
                continue
            except OSError:
                pass
        if b[:4] == b"PK\x03\x04":
            with zipfile.ZipFile(io.BytesIO(b)) as z:
                inner = [z.read(n) for n in sorted(z.namelist()) if not n.endswith("/") and "__MACOSX" not in n]
            out.extend(expand_archives(inner))
            continue
        if len(b) > 262 and b[257:262] == b"ustar":
            with tarfile.open(fileobj=io.BytesIO(b)) as t:
                inner = [t.extractfile(m).read() for m in sorted(t.getmembers(), key=lambda m: m.name) if m.isfile()]
            out.extend(expand_archives(inner))
            continue
        out.append(b)
    return out


def detect_provider(raw: bytes) -> str | None:
    """Best guess at which PBX produced a file (used to warn about a wrong dropdown choice)."""
    try:
        raw = expand_archives([raw])[0]
        head = _decode(raw[:4000]).splitlines()[0].lower()
    except Exception:  # noqa: BLE001
        return None
    if "dial status" in head and "called comments" in head:
        return "trusip"
    if "callee number" in head and ("session" in head or "main_cdr" in head) or head.startswith('"main_cdr"') \
            or head.startswith('"sub_cdr"'):
        return "grandstream"
    if re.search(r"\d{1,2} \w{3} \d{4} \d{2}:\d{2}:\d{2}", head) or "\t" in head:
        return "bicom"
    return None


def _clean_label(v: str) -> str:
    v = re.sub(r"(?i)\bdummy\b", "", str(v)).replace("_", " ")
    return re.sub(r"\s+", " ", v).strip()


def load_trusip(raw: bytes) -> pd.DataFrame:
    """Convert a TruSIP call-log export into the same leg format the Bicom reader produces.

    TruSIP columns: Unique ID, Date/Time (dd/mm/yyyy), Type (INBOUND/OUTBOUND), Dial Status,
    Caller Number, Caller Extension, Caller Comments, Called Number, Called Comments,
    Answering Extension, Duration (seconds), ...
    """
    text = _decode(raw)
    with warnings.catch_warnings():  # rows carry trailing commas beyond the header
        warnings.simplefilter("ignore")
        t = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False, index_col=False)
    t.columns = [c.strip().lower() for c in t.columns]
    t = t.apply(lambda c: c.str.strip())
    need = ["date/time", "type", "dial status", "caller number", "called number", "duration"]
    missing = [c for c in need if c not in t.columns]
    if missing:
        raise ValueError(f"not a TruSIP export - missing column(s): {', '.join(missing)}")
    for c in ("unique id", "caller comments", "called comments", "answering extension"):
        if c not in t:
            t[c] = ""

    t["ts"] = pd.to_datetime(t["date/time"], format="%d/%m/%Y %H:%M:%S", errors="coerce")
    if t["ts"].isna().mean() > 0.5:
        t["ts"] = pd.to_datetime(t["date/time"], dayfirst=True, errors="coerce")
    t = t.dropna(subset=["ts"]).reset_index(drop=True)
    t["type"] = t["type"].str.upper()
    t["secs"] = pd.to_numeric(t["duration"], errors="coerce").fillna(0).round().astype(int)
    t["caller_n"] = t["caller number"].map(norm_number)
    t["called_n"] = t["called number"].map(norm_number)

    inb = t[t["type"] == "INBOUND"]
    out = t[t["type"] == "OUTBOUND"]

    # Outbound rows that carry an outside caller's number within seconds of an inbound call from
    # that number are the forwarding leg of the inbound call - not a call made by the business.
    fwd = pd.Series(False, index=out.index)
    if len(inb) and len(out):
        by_caller = inb.groupby("caller_n")["ts"].apply(list).to_dict()
        for i, r in out.iterrows():
            for ts in by_caller.get(r["caller_n"], ()):
                if abs((r["ts"] - ts).total_seconds()) <= 15:
                    fwd[i] = True
                    break
    own_clis = set(out.loc[~fwd, "caller_n"]) - {""}
    dids = set(inb["called_n"]) - {""}
    own = own_clis | dids

    # Friendly names for the business's own numbers
    names: dict[str, str] = {}
    for num in own:
        cands = list(inb.loc[inb["called_n"] == num, "called comments"]) + \
            list(t.loc[t["caller_n"] == num, "caller comments"]) + list(out.loc[out["called_n"] == num, "called comments"])
        cands = [_clean_label(c) for c in cands if c and not c.lower().startswith("trunk")]
        cands = [c for c in cands if c]
        if cands:
            names[num] = pd.Series(cands).value_counts().index[0]

    internal_in = inb["caller_n"].isin(own)
    internal_out = out["called_n"].isin(own)
    keep_in = inb[~internal_in]
    keep_out = out[~fwd & ~internal_out]

    status_map = {"answered": "Answered", "missed": "Not Answered", "no answer": "Not Answered",
                  "busy": "Busy", "failed": "Failed", "cancelled": "Not Answered", "abandoned": "Not Answered"}

    def norm_status(v: str) -> str:
        v = str(v).lower()
        for k_, val in status_map.items():
            if v.startswith(k_):
                return val
        return "Failed"

    rows = []
    for i, r in keep_in.iterrows():
        st_ = norm_status(r["dial status"])
        secs = int(r["secs"])
        if st_ == "Answered":
            secs = max(secs, 1)  # TruSIP sometimes logs an answered call as 0s
        caller = r["caller_n"] if r["caller_n"] else (r["caller number"] or "anonymous")
        rows.append({
            "src": caller, "dst": r["called_n"], "start": r["date/time"], "duration": "", "talk": "",
            "cost": "", "status": st_, "uid": f"{int(r['ts'].timestamp())}.{i}", "clid": caller, "mos": "",
            "ts": r["ts"], "duration_s": secs if st_ == "Answered" else 0, "talk_s": secs if st_ == "Answered" else 0,
            "dst_label": names.get(r["called_n"], ""),
        })
    for i, r in keep_out.iterrows():
        cli = r["caller_n"]
        site = names.get(cli)
        who = f"{site} \u00b7 {pretty_number(cli)}" if site else pretty_number(cli)
        st_ = norm_status(r["dial status"])
        rows.append({
            "src": f"{who} ({re.sub(r'\\D', '', cli) or '0'})", "dst": r["called_n"], "start": r["date/time"],
            "duration": "", "talk": "", "cost": "", "status": st_, "uid": f"{int(r['ts'].timestamp())}.{i}",
            "clid": f"<{cli}>", "mos": "", "ts": r["ts"], "duration_s": int(r["secs"]), "talk_s": int(r["secs"]),
            "dst_label": "",
        })
    df = pd.DataFrame(rows)
    if df.empty:
        raise ValueError("no calls found in the TruSIP file")
    df["mos_v"] = np.nan
    df["cost_v"] = 0.0
    df["provider"] = "trusip"
    df.attrs["dropped"] = {"internal": int(internal_in.sum() + internal_out.sum()), "forwarded_legs": int(fwd.sum())}
    return df.sort_values("ts").reset_index(drop=True)


GS_STATUS = {"ANSWERED": "Answered", "NO ANSWER": "Not Answered", "BUSY": "Busy", "FAILED": "Failed"}


def load_grandstream(raw: bytes | list[bytes]) -> pd.DataFrame:
    """Convert Grandstream UCM CDR export(s) into the shared leg format.

    The UCM writes one `main_cdr` row per call plus `sub_cdr` rows for every leg. All rows of a call share a
    `Session`. For each inbound call we emit: the DID leg, one leg per ring-group stage (answered by the
    extension that picked up, or not answered), direct-to-extension legs and voicemail legs. That is the
    same shape as the Bicom export, so overflow, ring time and voicemail are all analysed the same way.
    """
    blobs = raw if isinstance(raw, list) else [raw]
    frames = []
    for b in expand_archives(blobs):
        t = pd.read_csv(io.StringIO(_decode(b)), dtype=str, keep_default_na=False)
        t.columns = [c.strip() for c in t.columns]
        need = ["CDR", "Caller Number", "Callee Number", "Start Time", "Call Status", "Call Type", "Session"]
        missing = [c for c in need if c not in t.columns]
        if missing:
            raise ValueError(f"not a Grandstream CDR export - missing column(s): {', '.join(missing)}")
        frames.append(t)
    return grandstream_from_table(pd.concat(frames, ignore_index=True))


def grandstream_from_table(t: pd.DataFrame) -> pd.DataFrame:
    """Same as load_grandstream, but from a table already in the UCM export's column layout
    (used by the live UCM API connection)."""
    t = t.astype(str).drop_duplicates()
    for c in ("Answer Time", "End Time", "Talk Time", "Call Time", "Action Type", "Last app.", "Caller Name",
              "Caller ID", "Unique ID", "Dest Channel Extension", "Context"):
        if c not in t:
            t[c] = ""
    t = t.apply(lambda c: c.str.strip())
    t["ts"] = pd.to_datetime(t["Start Time"], errors="coerce")
    t["ans_ts"] = pd.to_datetime(t["Answer Time"], errors="coerce")
    t["end_ts"] = pd.to_datetime(t["End Time"], errors="coerce")
    t["talk_n"] = pd.to_numeric(t["Talk Time"], errors="coerce").fillna(0).astype(int)
    t = t.dropna(subset=["ts"]).reset_index(drop=True)
    t["_ord"] = range(len(t))
    is_ext = t["Caller Number"].str.fullmatch(r"\d{2,5}")

    # extension -> person name, learned from calls the extension made
    names = (t[is_ext & (t["Caller Name"] != "") & ~t["Caller Name"].str.fullmatch(r"\+?\d+")]
             .groupby("Caller Number")["Caller Name"].agg(lambda s: s.value_counts().index[0]).to_dict())

    def ext_name(x: str) -> str:
        return f"{names.get(x, 'Ext ' + x)} ({x})"

    mains = t[t["CDR"] == "main_cdr"]
    subs = t[t["CDR"] != "main_cdr"]
    sub_by_session = {k: g.sort_values(["ts", "_ord"]) for k, g in subs.groupby("Session")}

    rows: list[dict] = []
    did_groups: dict[str, list[str]] = {}
    transfers = 0
    # per-user detail for the user assessment
    ring_events: list[dict] = []
    transfer_events: list[dict] = []
    for _, m in mains.iterrows():
        ctype = m["Call Type"].lower()
        session = m["Session"]
        caller = m["Caller Number"]
        if ctype == "outbound":
            ext = caller
            cli = re.search(r"<([^>]*)>", m["Caller ID"])
            st_ = GS_STATUS.get(m["Call Status"].upper(), "Failed")
            rows.append({"src": ext_name(ext), "dst": norm_number(m["Callee Number"]) or m["Callee Number"],
                         "status": st_, "uid": m["Unique ID"] or session, "clid": f"<{cli.group(1) if cli else ''}>",
                         "ts": m["ts"], "duration_s": int(pd.to_numeric(m["Call Time"], errors="coerce") or 0),
                         "talk_s": m["talk_n"], "dst_label": "", "pbx_status": ""})
            continue
        if ctype != "inbound":
            continue  # internal extension-to-extension calls
        # A colleague transferring an answered call creates a new session keyed on their extension
        if session.rsplit("-", 1)[-1] != caller and re.fullmatch(r"\d{2,5}", session.rsplit("-", 1)[-1] or ""):
            transfers += 1
            transfer_events.append({"ts": m["ts"], "from_ext": session.rsplit("-", 1)[-1],
                                    "to_ext": m["Callee Number"], "talk_s": int(m["talk_n"]),
                                    "answered": m["Call Status"].upper() == "ANSWERED" and m["talk_n"] > 0})
            continue
        legs = sub_by_session.get(session)
        if legs is None or legs.empty:
            legs = m.to_frame().T
        start = m["ts"]
        uid_sec = int(start.timestamp())
        caller_disp = caller if re.fullmatch(r"\+?\d{5,}", caller) else (caller or "anonymous")
        did = norm_number(m["Callee Number"]) if len(re.sub(r"\D", "", m["Callee Number"])) >= 8 else ""
        if not did:
            dl = legs[legs["Context"].str.startswith("ext-did")]
            did = norm_number(dl["Callee Number"].iloc[0]) if len(dl) else ""
        base = {"src": caller_disp, "clid": caller_disp}
        n = 0

        def add(dst, status, ts, dur, talk, label="", pbx=""):
            nonlocal n
            rows.append({**base, "dst": dst, "status": status, "uid": f"{uid_sec}.{len(rows) + 1}",
                         "ts": ts, "duration_s": int(max(0, dur)), "talk_s": int(max(0, talk)), "dst_label": label,
                         "pbx_status": pbx})
            n += 1

        # the line the call arrived on, carrying the UCM's own verdict on the call
        add(did or "01", "Answered", start, 0, 0, pbx=GS_STATUS.get(m["Call Status"].upper(), "Failed"))
        # group the legs into stages: each ring group, each direct extension dial, voicemail
        stages: list[tuple[str, pd.DataFrame]] = []
        for _, leg in legs.iterrows():
            act = leg["Action Type"].upper()
            app = leg["Last app."].lower()
            if "voicemail" in app or act.startswith("VM"):
                key = "VM"
            elif act.startswith("RINGGROUP"):
                key = act
            elif act == "DIAL" and re.fullmatch(r"\d{2,5}", leg["Callee Number"]) and app == "dial":
                key = f"EXT{leg['Callee Number']}"
            else:
                continue  # DID entry leg, IVR prompts, post-answer transfers etc.
            if stages and stages[-1][0] == key:
                stages[-1] = (key, pd.concat([stages[-1][1], leg.to_frame().T]))
            else:
                stages.append((key, leg.to_frame().T))
        answered_yet = False
        call_events: list[dict] = []
        for key, g in stages:
            g_start = g["ts"].min()
            g_end = g["end_ts"].max() if g["end_ts"].notna().any() else g_start
            if key == "VM":
                add(f"VM-{g['Callee Number'].iloc[0]} ({g['Callee Number'].iloc[0]})", "Answered", g_start,
                    (g_end - g_start).total_seconds(), int(g["talk_n"].max()))
                continue
            if answered_yet:
                continue
            pick = g[(g["Call Status"].str.upper() == "ANSWERED") & (g["talk_n"] > 0) &
                     g["Callee Number"].str.fullmatch(r"\d{2,5}") & (g["Last app."].str.lower() == "dial")]
            group_no = re.search(r"\[(\d+)\]", key)
            gname = f"Ring group {group_no.group(1)}" if group_no else ext_name(key[3:])
            if did and group_no:
                did_groups.setdefault(did, []).append(gname)
            answerer = pick.sort_values("ans_ts").iloc[0]["Callee Number"] if len(pick) else None
            rang = g[g["Callee Number"].str.fullmatch(r"\d{2,5}") & (g["Last app."].str.lower() == "dial")]
            for ext_, eg in rang.groupby("Callee Number"):
                ev = {"session": session, "ts": g_start, "ext": ext_,
                      "group": group_no.group(1) if group_no else "direct", "caller": caller_disp,
                      "answered": ext_ == answerer, "ring_s": np.nan, "talk_s": 0,
                      "window_s": max(0.0, (g_end - g_start).total_seconds()),
                      "declined": bool((eg["Call Status"].str.upper() == "BUSY").any())}
                if ext_ == answerer:
                    pa = pick[pick["Callee Number"] == ext_].sort_values("ans_ts").iloc[0]
                    ans_ = pa["ans_ts"] if pd.notna(pa["ans_ts"]) else g_start
                    ev["ring_s"] = max(0.0, (ans_ - g_start).total_seconds())
                    ev["talk_s"] = int(pa["talk_n"])
                call_events.append(ev)
            if len(pick):
                a = pick.sort_values("ans_ts").iloc[0]
                ans = a["ans_ts"] if pd.notna(a["ans_ts"]) else g_start
                ring = max(0.0, (ans - g_start).total_seconds())
                add(ext_name(a["Callee Number"]), "Answered", g_start, ring + a["talk_n"], a["talk_n"])
                answered_yet = True
            else:
                busy = (g["Call Status"].str.upper() == "BUSY").all()
                num = group_no.group(1) if group_no else key[3:]
                add(f"{gname} ({num})", "Busy" if busy else "Not Answered", g_start,
                    (g_end - g_start).total_seconds(), 0)
        for ev in call_events:
            ev["call_answered"] = answered_yet
            ev["call_start"] = start
        ring_events.extend(call_events)
        if not stages:
            # nothing rang: use the main record's own outcome
            st_ = GS_STATUS.get(m["Call Status"].upper(), "Failed")
            if st_ == "Answered" and m["talk_n"] > 0:
                add("Reception (0)", "Answered", start, int(pd.to_numeric(m["Call Time"], errors="coerce") or 0),
                    m["talk_n"])
            else:
                end = m["end_ts"] if pd.notna(m["end_ts"]) else start
                add("Line (0)", "Busy" if st_ == "Busy" else "Not Answered", start, (end - start).total_seconds(), 0)

    df = pd.DataFrame(rows)
    if df.empty:
        raise ValueError("no inbound or outbound calls found in the Grandstream file")
    # label each DID with the ring group it usually lands on
    labels = {d_: pd.Series(g).value_counts().index[0] for d_, g in did_groups.items()}
    df.loc[df["dst"].isin(labels.keys()), "dst_label"] = df["dst"].map(labels)
    df["start"] = df["ts"].dt.strftime("%d %b %Y %H:%M:%S")
    for c in ("duration", "talk", "cost", "mos"):
        df[c] = ""
    df["mos_v"] = np.nan
    df["cost_v"] = 0.0
    df["provider"] = "grandstream"
    internal = mains[mains["Call Type"].str.lower() == "internal"]
    df.attrs["users"] = {
        "names": names,
        "ring": pd.DataFrame(ring_events),
        "transfers": pd.DataFrame(transfer_events),
        "internal": pd.DataFrame({"ts": internal["ts"], "from_ext": internal["Caller Number"],
                                  "to_ext": internal["Callee Number"], "talk_s": internal["talk_n"],
                                  "status": internal["Call Status"].str.upper()}).reset_index(drop=True),
        "outbound": pd.DataFrame({"ts": mains.loc[mains["Call Type"].str.lower() == "outbound", "ts"],
                                  "ext": mains.loc[mains["Call Type"].str.lower() == "outbound", "Caller Number"],
                                  "dialled": mains.loc[mains["Call Type"].str.lower() == "outbound", "Callee Number"]
                                  .map(norm_number),
                                  "talk_s": mains.loc[mains["Call Type"].str.lower() == "outbound", "talk_n"],
                                  "status": mains.loc[mains["Call Type"].str.lower() == "outbound", "Call Status"]
                                  .str.upper()}).reset_index(drop=True),
    }
    df.attrs["dropped"] = {"transfers": transfers,
                           "internal": int(((mains["Call Type"].str.lower() == "internal")).sum())}
    return df.sort_values("ts").reset_index(drop=True)


def load_many(files: list[bytes], provider: str = "bicom") -> pd.DataFrame:
    if provider == "grandstream":
        df = load_grandstream(list(files))
        df["provider"] = provider
        return df
    files = expand_archives(list(files))
    loader = load_trusip if provider == "trusip" else load_cdr
    frames = [loader(f) for f in files]
    if len(frames) == 1:
        df = frames[0]
    else:
        # overlapping exports: drop rows that appear in more than one file
        df = pd.concat(frames, ignore_index=True)
        key = ["src", "dst", "start", "status"] + (["uid"] if provider != "trusip" else [])
        df = df.drop_duplicates(subset=key).reset_index(drop=True)
    df["provider"] = provider
    return df


def _to_seconds(v) -> int:
    s = str(v).strip()
    if not s:
        return 0
    if ":" in s:
        parts = [int(float(p)) for p in s.split(":")]
        while len(parts) < 3:
            parts.insert(0, 0)
        h, m, sec = parts[-3:]
        return h * 3600 + m * 60 + sec
    try:
        return int(float(s))
    except ValueError:
        return 0


# --------------------------------------------------------------------------- #
# Classifying endpoints
# --------------------------------------------------------------------------- #
EXT_RE = re.compile(r"^(?P<name>.*?)\s*\((?P<ext>\d+)\)\s*$")


def norm_number(v: str) -> str:
    """+447700900123 / 447700900123 / 07700 900123 -> 07700900123."""
    s = str(v)
    digits = re.sub(r"\D", "", s)
    if not digits:
        return ""
    if digits.startswith("44") and len(digits) >= 11:
        return "0" + digits[2:]
    if digits.startswith("0"):
        return digits
    if s.strip().startswith("+"):
        return "+" + digits
    return digits


def endpoint_kind(v: str) -> str:
    s = str(v).strip()
    if not s:
        return "UNKNOWN"
    m = EXT_RE.match(s)
    if m:
        name = m.group("name").lower()
        if name.startswith("vm") or "voicemail" in name:
            return "VM"
        return "EXT"
    if "*" in s or s.startswith("#"):
        return "FEATURE"
    if re.fullmatch(r"\+?\d{5,}", s):
        return "NUM"
    if s.lower() in ("anonymous", "withheld", "unknown", "private", "restricted"):
        return "ANON"
    if re.fullmatch(r"\+?\d{1,4}", s):
        return "EXT"
    return "OTHER"


def pretty_number(n: str) -> str:
    if not n:
        return "Withheld"
    if n.startswith("07") and len(n) == 11:
        return f"{n[:5]} {n[5:]}"
    if n.startswith(("011", "012", "013", "014", "015", "016", "017", "018", "019")) and len(n) == 11:
        if n[2] == "1" or n[3] == "1":  # 0161 / 0113 style
            return f"{n[:4]} {n[4:7]} {n[7:]}"
        return f"{n[:5]} {n[5:]}"
    if n.startswith(("02",)) and len(n) == 11:
        return f"{n[:3]} {n[3:7]} {n[7:]}"
    if n.startswith(("03", "08")) and len(n) == 11:
        return f"{n[:4]} {n[4:7]} {n[7:]}"
    return n


def ext_label(v: str) -> str:
    m = EXT_RE.match(str(v).strip())
    return m.group("name").strip() if m else str(v)


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
@dataclass
class Settings:
    open_time: time = time(8, 30)
    close_time: time = time(17, 30)
    open_days: tuple = (0, 1, 2, 3, 4)  # Mon-Fri
    short_abandon_s: int = 5            # caller hung up before this many seconds of ringing
    answer_target_pct: float = 90.0
    callback_target_min: int = 60       # target time to return a missed call
    pending_hours: int = 4              # missed calls this close to the data end are "too recent to judge"
    exclude_numbers: tuple = ()         # nuisance / spam callers to leave out entirely
    exclude_international: bool = False


# --------------------------------------------------------------------------- #
# Stitching legs into calls
# --------------------------------------------------------------------------- #
OUTCOME_ORDER = ["Answered", "Answered via overflow", "Voicemail", "Missed", "Busy", "Short abandon", "Failed"]
MISSED_OUTCOMES = {"Voicemail", "Missed", "Busy"}


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["src_kind"] = df["src"].map(endpoint_kind)
    df["dst_kind"] = df["dst"].map(endpoint_kind)
    df["src_num"] = df["src"].map(lambda v: norm_number(v) if endpoint_kind(v) == "NUM" else "")
    df["dst_num"] = df["dst"].map(lambda v: norm_number(v) if endpoint_kind(v) == "NUM" else "")

    def direction(r):
        if r.dst_kind == "FEATURE" or r.src_kind == "FEATURE":
            return "feature"
        if r.src_kind in ("EXT", "VM"):
            if r.dst_kind == "NUM":
                return "outbound"
            return "internal"
        if r.src_kind in ("NUM", "ANON", "OTHER", "UNKNOWN"):
            return "inbound"
        return "other"

    df["direction"] = df.apply(direction, axis=1)
    df["uid_sec"] = df["uid"].astype(str).str.split(".").str[0]
    df["uid_frac"] = pd.to_numeric(df["uid"].astype(str).str.split(".").str[1], errors="coerce").fillna(0)
    return df


def line_labels(df: pd.DataFrame) -> dict[str, str]:
    """Name each DID after the extension that most often uses it as outbound CLI."""
    out = df[(df["direction"] == "outbound")].copy()
    out["cli"] = out["clid"].map(lambda v: norm_number(re.sub(r"[<>]", "", str(v))))
    out["who"] = out["src"].map(ext_label)
    labels = {}
    for cli, g in out.groupby("cli"):
        if cli:
            labels[cli] = g["who"].value_counts().index[0]
    # DIDs that front an IVR (system answers, then routes on) are labelled as such
    inb = df[df["direction"] == "inbound"]
    multi = inb.groupby("uid_sec").filter(lambda g: len(g) > 1)
    ivr_hits = {}
    for _, g in multi.sort_values(["ts", "uid_frac"]).groupby("uid_sec"):
        first = g.iloc[0]
        if first["dst_kind"] == "NUM" and first["status"] == "Answered" and (g["dst_kind"] != "NUM").any():
            ivr_hits[first["dst_num"]] = ivr_hits.get(first["dst_num"], 0) + 1
    for did, n in ivr_hits.items():
        if n >= 5:
            labels[did] = "IVR / main menu"
    # Providers that name their inbound numbers (e.g. TruSIP "Chester_Sales Dummy")
    if "dst_label" in df:
        named = inb[inb["dst_label"].fillna("") != ""]
        for did, g in named.groupby("dst_num"):
            if did:
                labels[did] = g["dst_label"].value_counts().index[0]
    final = {}
    for did, name in labels.items():
        pn = pretty_number(did)
        final[did] = f"{name} \u00b7 {pn}" if did.startswith("0") and pn not in name else name
    return final


def build_inbound_calls(df: pd.DataFrame) -> pd.DataFrame:
    inbound = df[df["direction"] == "inbound"].copy()
    inbound["caller"] = inbound["src_num"]
    who = inbound["caller"].where(inbound["caller"] != "", inbound["src"])
    # Legs of one call usually share a unique-id, but some PBX routes (e.g. DID -> direct extension)
    # spawn a new id a second or two later. Chain ids from the same caller that start within 3s.
    sec = pd.to_numeric(inbound["uid_sec"], errors="coerce").fillna(
        inbound["ts"].astype("int64") // 10**9).astype("int64")
    inbound["_sec"] = sec
    inbound["_who"] = who
    inbound = inbound.sort_values(["_who", "_sec", "ts", "uid_frac"])
    new_call = (inbound["_who"] != inbound["_who"].shift()) | (inbound["_sec"].diff().fillna(99) > 3)
    inbound["gkey"] = inbound["_who"] + "|" + new_call.cumsum().astype(str)
    inbound = inbound.sort_values(["gkey", "ts", "uid_frac"])

    rows = []
    for key, g in inbound.groupby("gkey", sort=False):
        legs = g.to_dict("records")
        first = legs[0]
        non_vm = [l for l in legs if l["dst_kind"] != "VM"]
        vm_legs = [l for l in legs if l["dst_kind"] == "VM"]

        answer_leg = None
        for l in non_vm:
            if l["dst_kind"] == "EXT" and l["status"] == "Answered" and (l["talk_s"] > 0 or l["duration_s"] > 0):
                answer_leg = l
                break
        if answer_leg is None and non_vm:
            last = non_vm[-1]
            later_vm = any(v["ts"] >= last["ts"] for v in vm_legs)
            if (last["dst_kind"] == "NUM" and last["status"] == "Answered"
                    and (last["talk_s"] > 0 or last["duration_s"] > 0) and not later_vm):
                answer_leg = last

        end_ts = max(l["ts"] + timedelta(seconds=int(l["duration_s"])) for l in legs)
        wait_total = (end_ts - first["ts"]).total_seconds()

        detail = ""
        if answer_leg is not None:
            idx = non_vm.index(answer_leg)
            prior_unanswered = [l for l in non_vm[:idx] if l["status"] != "Answered"]
            outcome = "Answered via overflow" if prior_unanswered else "Answered"
            ring = (answer_leg["ts"] - first["ts"]).total_seconds() + max(
                0, answer_leg["duration_s"] - answer_leg["talk_s"])
            talk = answer_leg["talk_s"]
            answered_by = ext_label(answer_leg["dst"]) if answer_leg["dst_kind"] == "EXT" else ""
        else:
            statuses = {l["status"] for l in legs}
            ring = wait_total
            talk = 0
            answered_by = ""
            if any(v["status"] == "Answered" for v in vm_legs):
                outcome = "Voicemail"
                rang_someone = any(l["dst_kind"] == "EXT" or l["status"] != "Answered" for l in non_vm)
                detail = "after ringing" if rang_someone else "straight from menu / closed"
            elif statuses <= {"Failed"}:
                outcome = "Failed"
            elif "Busy" in statuses and "Not Answered" not in statuses:
                outcome = "Busy"
            elif wait_total < 0:
                outcome = "Missed"
            else:
                outcome = "Missed"

        line_leg = next((l for l in legs if l["dst_kind"] == "NUM"), first)
        rows.append({
            "call_id": key,
            "ts": first["ts"],
            "caller": first["caller"],
            "caller_raw": first["src"],
            "line": line_leg["dst_num"] or ext_label(line_leg["dst"]),
            "line_raw": line_leg["dst"],
            "legs": len(legs),
            "route": " > ".join(
                f"{('VM ' if l['dst_kind']=='VM' else '')}{ext_label(l['dst']) if l['dst_kind']!='NUM' else 'Line'}:"
                f"{l['status'].replace('Not Answered','NA')}" for l in legs),
            "outcome": outcome,
            "ring_s": max(0, ring),
            "talk_s": talk,
            "answered_by": answered_by,
            "detail": detail,
            "mos": np.nanmax([l["mos_v"] if l["mos_v"] and l["mos_v"] > 0 else np.nan for l in legs])
            if any(l["mos_v"] and l["mos_v"] > 0 for l in legs) else np.nan,
        })
    calls = pd.DataFrame(rows)
    if calls.empty:
        return calls
    return calls.sort_values("ts").reset_index(drop=True)


def build_outbound_calls(df: pd.DataFrame) -> pd.DataFrame:
    out = df[df["direction"] == "outbound"].copy()
    out = out.sort_values(["uid", "ts"])
    calls = out.groupby("uid", sort=False).agg(
        ts=("ts", "min"),
        dialled=("dst_num", "first"),
        agent=("src", lambda s: ext_label(s.iloc[0])),
        talk_s=("talk_s", "max"),
        duration_s=("duration_s", "max"),
        status=("status", lambda s: "Answered" if (s == "Answered").any() else s.iloc[0]),
        cost=("cost_v", "sum"),
        mos=("mos_v", lambda s: s[s > 0].max() if (s > 0).any() else np.nan),
    ).reset_index()
    calls["connected"] = (calls["status"] == "Answered") & (calls["talk_s"] > 0)
    return calls.sort_values("ts").reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Missed-call follow-up tracking
# --------------------------------------------------------------------------- #
def track_returns(inb: pd.DataFrame, outb: pd.DataFrame, data_end: datetime, s: Settings) -> pd.DataFrame:
    """For every truly missed call, did we ring them back - or did they get through later?"""
    missed = inb[inb["outcome"].isin(MISSED_OUTCOMES)].copy()
    if missed.empty:
        return missed.assign(follow_up=[], follow_up_ts=[], follow_up_by=[], minutes_to_follow_up=[])

    out_by_num = {n: g.sort_values("ts") for n, g in outb.groupby("dialled")}
    answered_in = inb[inb["outcome"].isin(["Answered", "Answered via overflow"])]
    in_by_num = {n: g.sort_values("ts") for n, g in answered_in.groupby("caller")}

    res = []
    for r in missed.itertuples():
        status, f_ts, by = "Not returned", pd.NaT, ""
        if not r.caller:
            status = "Withheld number"
        else:
            o = out_by_num.get(r.caller)
            i = in_by_num.get(r.caller)
            o_after = o[o["ts"] > r.ts] if o is not None else None
            i_after = i[i["ts"] > r.ts] if i is not None else None
            conn = o_after[o_after["connected"]] if o_after is not None else None
            cands = []
            if conn is not None and len(conn):
                cands.append((conn["ts"].iloc[0], "Called back - spoke", conn["agent"].iloc[0]))
            if i_after is not None and len(i_after):
                cands.append((i_after["ts"].iloc[0], "Customer rang again - answered", i_after["answered_by"].iloc[0]))
            if cands:
                f_ts, status, by = min(cands, key=lambda c: c[0])
            elif o_after is not None and len(o_after):
                f_ts, status, by = o_after["ts"].iloc[0], "Call-back attempted - no answer", o_after["agent"].iloc[0]
            elif (data_end - r.ts) < timedelta(hours=s.pending_hours):
                status = "Too recent to judge"
        mins = (f_ts - r.ts).total_seconds() / 60 if pd.notna(f_ts) else np.nan
        res.append((status, f_ts, by, mins))
    missed["follow_up"] = [x[0] for x in res]
    missed["follow_up_ts"] = [x[1] for x in res]
    missed["follow_up_by"] = [x[2] for x in res]
    missed["minutes_to_follow_up"] = [x[3] for x in res]
    return missed


RESOLVED = {"Called back - spoke", "Customer rang again - answered"}


# --------------------------------------------------------------------------- #
# The full analysis
# --------------------------------------------------------------------------- #
@dataclass
class Analysis:
    period_start: date
    period_end: date
    inbound: pd.DataFrame
    outbound: pd.DataFrame
    missed: pd.DataFrame
    kpis: dict
    hourly: pd.DataFrame
    heat: pd.DataFrame
    daily: pd.DataFrame
    lines: pd.DataFrame
    team: pd.DataFrame
    unreturned_callers: pd.DataFrame
    observations: list = field(default_factory=list)
    recommendations: list = field(default_factory=list)
    summary: str = ""
    settings: Settings = field(default_factory=Settings)
    provider: str = "bicom"
    caps: dict = field(default_factory=lambda: dict(CAPS["bicom"]))


def in_hours(ts: pd.Series, s: Settings) -> pd.Series:
    t = ts.dt.time
    return ts.dt.dayofweek.isin(s.open_days) & (t >= s.open_time) & (t < s.close_time)


def fmt_dur(seconds: float) -> str:
    if seconds is None or (isinstance(seconds, float) and np.isnan(seconds)):
        return "-"
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"


def fmt_mins(m: float) -> str:
    if m is None or np.isnan(m):
        return "-"
    if m < 60:
        return f"{m:.0f} min"
    if m < 60 * 24:
        return f"{m / 60:.1f} hrs"
    return f"{m / 1440:.1f} days"


def _pbx_missed(df: pd.DataFrame, lo, hi) -> int:
    """How many inbound calls the phone system's own log calls unanswered in the period."""
    win = df[(df["direction"] == "inbound") & (df["ts"] >= lo) & (df["ts"] < hi)]
    if "pbx_status" in win and (win["pbx_status"].fillna("") != "").any():
        # one verdict per call (Grandstream main_cdr)
        return int(win["pbx_status"].isin(["Not Answered", "Busy"]).sum())
    return int(win["status"].isin(["Not Answered", "Busy"]).sum())


def analyse(raw_df: pd.DataFrame, start: date, end: date, s: Settings | None = None) -> Analysis:
    s = s or Settings()
    provider = str(raw_df["provider"].iloc[0]) if "provider" in raw_df and len(raw_df) else "bicom"
    caps = dict(CAPS.get(provider, CAPS["bicom"]))
    df = prepare(raw_df)
    labels = line_labels(df)
    data_end = df["ts"].max()

    inb_all = build_inbound_calls(df)
    excl = {norm_number(n) for n in s.exclude_numbers if norm_number(n)}
    drop = inb_all["caller"].isin(excl)
    if s.exclude_international:
        drop |= inb_all["caller"].str.startswith("+")
    inb_all = inb_all[~drop].reset_index(drop=True)
    outb_all = build_outbound_calls(df)

    # short abandons: caller gave up almost immediately - not a real chance to answer
    # (only possible when the export records how long the call rang)
    if caps["ring_time"]:
        sa = inb_all["outcome"].isin(["Missed"]) & (inb_all["ring_s"] < s.short_abandon_s)
        inb_all.loc[sa, "outcome"] = "Short abandon"

    missed_all = track_returns(inb_all, outb_all, data_end, s)

    lo, hi = pd.Timestamp(start), pd.Timestamp(end) + pd.Timedelta(days=1)
    inb = inb_all[(inb_all["ts"] >= lo) & (inb_all["ts"] < hi)].copy()
    outb = outb_all[(outb_all["ts"] >= lo) & (outb_all["ts"] < hi)].copy()
    missed = missed_all[(missed_all["ts"] >= lo) & (missed_all["ts"] < hi)].copy()

    inb["in_hours"] = in_hours(inb["ts"], s) if len(inb) else []
    missed["in_hours"] = in_hours(missed["ts"], s) if len(missed) else []
    inb["line_name"] = inb["line"].map(lambda n: labels.get(n, pretty_number(n) if str(n).startswith("0") else f"{n} (direct)"))
    missed["line_name"] = missed["line"].map(lambda n: labels.get(n, pretty_number(n) if str(n).startswith("0") else f"{n} (direct)"))

    counted = inb[~inb["outcome"].isin(["Short abandon", "Failed"])]
    counted_ih = counted[counted["in_hours"]]
    ans_mask = counted["outcome"].isin(["Answered", "Answered via overflow"])
    ans_ih = counted_ih["outcome"].isin(["Answered", "Answered via overflow"])
    answered = counted[ans_mask]
    overflow = counted[counted["outcome"] == "Answered via overflow"]

    m_ih = missed[missed["in_hours"]]
    judged = missed[~missed["follow_up"].isin(["Withheld number", "Too recent to judge"])]
    resolved = judged[judged["follow_up"].isin(RESOLVED)]
    returned_by_us = judged[judged["follow_up"] == "Called back - spoke"]

    # callers whose LAST missed call in the period was never followed up
    unret = (missed.sort_values("ts").groupby("caller").tail(1))
    unret = unret[unret["follow_up"].isin(["Not returned", "Call-back attempted - no answer"]) & (unret["caller"] != "")]
    per_caller = missed[missed["caller"] != ""].groupby("caller").agg(
        missed_calls=("call_id", "count"), first_missed=("ts", "min"), last_missed=("ts", "max"))
    unreturned_callers = unret.set_index("caller")[["follow_up", "line_name"]].join(per_caller).reset_index()
    unreturned_callers = unreturned_callers.sort_values(["missed_calls", "last_missed"], ascending=[False, False])

    days = max(1, (end - start).days + 1)
    open_days_n = sum(1 for i in range(days) if (start + timedelta(days=i)).weekday() in s.open_days)

    k = {
        "inbound_total": len(counted),
        "inbound_raw_rows": int((df["direction"] == "inbound").sum()),
        "inbound_in_hours": len(counted_ih),
        "answered": len(answered),
        "answered_first_time": int((counted["outcome"] == "Answered").sum()),
        "answered_overflow": len(overflow),
        "answer_rate": 100 * len(answered) / len(counted) if len(counted) else 0,
        "answer_rate_ih": 100 * ans_ih.sum() / len(counted_ih) if len(counted_ih) else 0,
        "true_missed": len(missed),
        "true_missed_ih": len(m_ih),
        "true_missed_ooh": len(missed) - len(m_ih),
        "voicemail": int((missed["outcome"] == "Voicemail").sum()),
        "voicemail_direct": int(((missed["outcome"] == "Voicemail") & (missed["detail"] != "after ringing")).sum()),
        "pbx_reported_missed": _pbx_missed(df, lo, hi),
        "short_abandons": int((inb["outcome"] == "Short abandon").sum()),
        "failed": int((inb["outcome"] == "Failed").sum()),
        "missed_judged": len(judged),
        "missed_resolved": len(resolved),
        "missed_returned_by_us": len(returned_by_us),
        "missed_resolved_pct": 100 * len(resolved) / len(judged) if len(judged) else 0,
        "unreturned_calls": int(judged["follow_up"].isin(["Not returned", "Call-back attempted - no answer"]).sum()),
        "unreturned_callers": len(unreturned_callers),
        "attempt_only": int((judged["follow_up"] == "Call-back attempted - no answer").sum()),
        "withheld_missed": int((missed["follow_up"] == "Withheld number").sum()),
        "median_callback_min": float(returned_by_us["minutes_to_follow_up"].median()) if len(returned_by_us) else np.nan,
        "callback_within_target_pct": 100 * (returned_by_us["minutes_to_follow_up"] <= s.callback_target_min).mean()
        if len(returned_by_us) else np.nan,
        "avg_ring_s": float(answered["ring_s"].mean()) if len(answered) and caps["ring_time"] else np.nan,
        "avg_ring_first_s": float(answered[answered["outcome"] == "Answered"]["ring_s"].mean())
        if len(answered) and caps["ring_time"] else np.nan,
        "busy": int((missed["outcome"] == "Busy").sum()),
        "avg_talk_s": float(answered["talk_s"].mean()) if len(answered) else np.nan,
        "outbound_total": len(outb),
        "outbound_connected": int(outb["connected"].sum()),
        "outbound_talk_s": float(outb["talk_s"].sum()),
        "outbound_cost": float(outb["cost"].sum()),
        "days": days,
        "open_days": open_days_n,
        "avg_inbound_per_open_day": len(counted_ih) / open_days_n if open_days_n else 0,
        "unique_callers": counted["caller"].replace("", np.nan).nunique(),
        "mos_avg": float(pd.concat([inb["mos"], outb["mos"]]).dropna().mean()) if len(inb) + len(outb) else np.nan,
        "mos_poor_pct": float((pd.concat([inb["mos"], outb["mos"]]).dropna() < 3.5).mean() * 100)
        if pd.concat([inb["mos"], outb["mos"]]).dropna().size else np.nan,
    }

    # hourly profile (all days)
    hrs = counted.assign(hour=counted["ts"].dt.hour, ans=ans_mask)
    hourly = hrs.groupby("hour").agg(calls=("call_id", "count"), answered=("ans", "sum")).reindex(range(24), fill_value=0)
    hourly["missed"] = hourly["calls"] - hourly["answered"]
    hourly["avg_per_day"] = hourly["calls"] / max(1, open_days_n)
    hourly["miss_rate"] = np.where(hourly["calls"] > 0, 100 * hourly["missed"] / hourly["calls"].clip(lower=1), 0)

    heat = hrs.assign(dow=hrs["ts"].dt.dayofweek).pivot_table(
        index="dow", columns="hour", values="call_id", aggfunc="count", fill_value=0)
    heat = heat.reindex(index=range(7), columns=range(24), fill_value=0)

    daily = hrs.assign(day=hrs["ts"].dt.date).groupby("day").agg(calls=("call_id", "count"), answered=("ans", "sum"))
    daily["missed"] = daily["calls"] - daily["answered"]

    lines = counted.assign(ans=ans_mask, ovf=counted["outcome"] == "Answered via overflow",
                           bsy=counted["outcome"] == "Busy").groupby("line_name").agg(
        calls=("call_id", "count"), answered=("ans", "sum"), overflow=("ovf", "sum"), busy=("bsy", "sum"))
    lines["missed"] = lines["calls"] - lines["answered"]
    lines["answer_rate"] = 100 * lines["answered"] / lines["calls"]
    unret_by_line = unreturned_callers.groupby("line_name").size()
    lines["unreturned_callers"] = unret_by_line.reindex(lines.index).fillna(0).astype(int)
    lines = lines.sort_values("calls", ascending=False)

    team = outb.groupby("agent").agg(outbound=("uid", "count"), connected=("connected", "sum"),
                                     talk_s=("talk_s", "sum")).sort_values("outbound", ascending=False)
    cb = returned_by_us.groupby("follow_up_by").size()
    team["missed_calls_returned"] = cb.reindex(team.index).fillna(0).astype(int)

    a = Analysis(start, end, inb, outb, missed, k, hourly, heat, daily, lines, team, unreturned_callers, settings=s,
                 provider=provider, caps=caps)
    _write_insights(a)
    return a


# --------------------------------------------------------------------------- #
# Plain-English insight generator
# --------------------------------------------------------------------------- #
def _write_insights(a: Analysis) -> None:
    k, s = a.kpis, a.settings
    obs, rec = [], []
    if k["inbound_total"] == 0:
        a.summary = "No inbound calls were found in the selected period."
        return

    ar = k["answer_rate_ih"]
    hidden = max(0, k["pbx_reported_missed"] - k["true_missed"])
    routing = a.caps.get("routing", True)
    if routing:
        middle = (f"The phone system's own log shows {k['pbx_reported_missed']:,} 'not answered' entries, but once "
                  f"ring-group overflow and call routing are stitched together only {k['true_missed']:,} calls were "
                  f"truly missed. ")
    else:
        middle = (f"{k['true_missed']:,} calls were missed"
                  + (f", including {k['busy']:,} callers who heard an engaged tone" if k["busy"] else "") + ". ")
    a.summary = (
        f"Between {a.period_start:%d %b %Y} and {a.period_end:%d %b %Y} the business received "
        f"{k['inbound_total']:,} genuine inbound calls ({k['avg_inbound_per_open_day']:.0f} per working day). "
        f"During opening hours {ar:.1f}% were answered"
        f"{' - above' if ar >= s.answer_target_pct else ' - below'} the {s.answer_target_pct:.0f}% benchmark. "
        + middle +
        f"Of the missed calls we can judge, {k['missed_resolved_pct']:.0f}% were followed up successfully, leaving "
        f"{k['unreturned_callers']:,} callers who never got a conversation."
    )

    if hidden > 0 and routing:
        why = ("answered by an overflow group, are internal routing legs, or were callers hanging up within "
               f"{s.short_abandon_s}s")
        obs.append(f"{hidden:,} of the phone system's 'not answered' entries were not genuine lost calls - they were "
                   f"{why}. The raw log overstates missed calls by "
                   f"{100 * hidden / max(1, k['pbx_reported_missed']):.0f}%.")
    if k["answered"] and not routing:
        obs.append(f"Answered calls lasted {fmt_dur(k['avg_talk_s'])} on average.")
    if k["busy"]:
        bp = 100 * k["busy"] / max(1, k["true_missed"])
        obs.append(f"{k['busy']:,} callers ({bp:.0f}% of missed calls) got an engaged tone - every line or handset "
                   f"was busy, so the call never rang through.")
        if k["busy"] >= 10 or bp >= 15:
            rec.append("Reduce engaged tones: check the number of call channels, and enable call waiting or a "
                       "queue / overflow so busy-time callers are held or redirected instead of turned away.")
    if k["answered"] and routing and not k["answered_overflow"]:
        obs.append(f"Every answered call was picked up by the first ring group it reached, in "
                   f"{fmt_dur(k['avg_ring_s'])} on average.")
    if k["answered"] and routing and k["answered_overflow"]:
        ov = 100 * k["answered_overflow"] / k["answered"]
        obs.append(f"{ov:.0f}% of answered calls ({k['answered_overflow']:,}) were only picked up after the first ring "
                   f"group timed out. Average time to answer was {fmt_dur(k['avg_ring_s'])} overall versus "
                   f"{fmt_dur(k['avg_ring_first_s'])} when the first group answered.")
        if ov >= 15:
            rec.append("Review the first ring group: too many calls fall through to overflow. Add users to the first "
                       "group, or shorten its timeout so callers reach an available person sooner.")

    hr = a.hourly[a.hourly["calls"] > 0]
    if len(hr):
        peak = hr["calls"].nlargest(3).index.tolist()
        peak_txt = ", ".join(f"{h:02d}:00-{h + 1:02d}:00" for h in sorted(peak))
        obs.append(f"Busiest hours are {peak_txt}, carrying "
                   f"{100 * hr.loc[peak, 'calls'].sum() / hr['calls'].sum():.0f}% of all inbound calls.")
        worst = hr[hr["calls"] >= max(5, hr["calls"].sum() * 0.02)]["miss_rate"]
        if len(worst):
            wh = worst.idxmax()
            if worst.max() > (100 - ar) * 1.4 and worst.max() >= 10:
                obs.append(f"The worst hour for missed calls is {wh:02d}:00-{wh + 1:02d}:00, when {worst.max():.0f}% "
                           f"of calls went unanswered.")
                rec.append(f"Put cover in place around {wh:02d}:00 (e.g. staggered breaks or a dedicated answering "
                           f"rota) - that is where calls are being lost.")

    if k["true_missed_ooh"] > 0:
        pct = 100 * k["true_missed_ooh"] / max(1, k["true_missed"])
        obs.append(f"{k['true_missed_ooh']:,} missed calls ({pct:.0f}%) arrived outside opening hours "
                   f"({s.open_time:%H:%M}-{s.close_time:%H:%M}).")
        if pct >= 20 or k["true_missed_ooh"] >= 20:
            rec.append("Consider an out-of-hours / overflow call-answering service so early, late and lunchtime "
                       "callers speak to a person and a message is captured.")

    if k["voicemail"]:
        obs.append(f"{k['voicemail']:,} callers ended in voicemail - {k['voicemail_direct']:,} went straight there from "
                   f"the menu or because the line was closed, the rest after nobody answered.")
        rec.append("Make sure every voicemail box is delivered to a shared email or team inbox, with an agreed owner, "
                   "so messages are actioned the same day.")

    if k["missed_judged"]:
        parts = [f"{k['missed_returned_by_us']:,} were called back and connected",
                 f"{k['missed_resolved'] - k['missed_returned_by_us']:,} customers rang again and got through"]
        if k["attempt_only"]:
            parts.append(f"{k['attempt_only']:,} had a call-back attempt that did not connect")
        parts.append(f"{k['unreturned_calls'] - k['attempt_only']:,} were never returned at all")
        obs.append("Missed-call follow-up: " + ", ".join(parts[:-1]) + " and " + parts[-1] + ".")
    if not np.isnan(k["median_callback_min"]):
        obs.append(f"Typical (median) time to return a missed call was {fmt_mins(k['median_callback_min'])}; "
                   f"{k['callback_within_target_pct']:.0f}% were returned within {s.callback_target_min} minutes.")
        if k["median_callback_min"] > s.callback_target_min:
            rec.append(f"Set a call-back target of {s.callback_target_min} minutes for missed calls and assign "
                       f"ownership per line/branch.")
    if k["unreturned_callers"] > 0:
        rec.append(f"Work through the {k['unreturned_callers']:,} un-returned callers listed in this report - "
                   f"each one is a potential lost customer or complaint.")
        rec.append("Enable missed-call notifications (email/app or CRM task) so nobody relies on manually "
                   "checking the call log.")

    if len(a.lines) > 1:
        l2 = a.lines[a.lines["calls"] >= max(10, k["inbound_total"] * 0.02)]
        if len(l2):
            w = l2["answer_rate"].idxmin()
            if l2.loc[w, "answer_rate"] < ar - 5:
                obs.append(f"'{w}' has the lowest answer rate at {l2.loc[w, 'answer_rate']:.0f}% "
                           f"({int(l2.loc[w, 'missed'])} missed of {int(l2.loc[w, 'calls'])}).")
                rec.append(f"Review staffing and the hunt/overflow group for '{w}'.")

    if k["short_abandons"]:
        obs.append(f"{k['short_abandons']:,} callers hung up within {s.short_abandon_s}s of ringing - these are "
                   f"excluded from the missed figures.")
    if not np.isnan(k.get("mos_poor_pct", np.nan)) and k["mos_poor_pct"] >= 5:
        obs.append(f"Call quality: average MOS {k['mos_avg']:.2f}; {k['mos_poor_pct']:.0f}% of measured calls scored "
                   f"below 3.5 (noticeably degraded audio).")
        rec.append("Investigate network quality for VoIP (bandwidth, QoS on the router, Wi-Fi handsets) - a "
                   "proportion of calls had poor audio scores.")

    if ar >= s.answer_target_pct and not rec:
        rec.append("Call handling is strong - keep monitoring monthly to protect this standard.")
    a.observations, a.recommendations = obs, rec[:7]


def detect_range(df: pd.DataFrame) -> tuple[date, date]:
    return df["ts"].min().date(), df["ts"].max().date()


def preset_range(name: str, data_min: date, data_max: date) -> tuple[date, date]:
    ref = data_max
    if name == "This month":
        return max(data_min, ref.replace(day=1)), ref
    if name == "Last month":
        first_this = ref.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        return max(data_min, last_prev.replace(day=1)), last_prev
    if name == "Last 7 days":
        return max(data_min, ref - timedelta(days=6)), ref
    if name == "Last 30 days":
        return max(data_min, ref - timedelta(days=29)), ref
    return data_min, data_max
