"""
users.py - per-person ("user") assessment.

Grandstream UCM exports record every handset that rang on every call, so for each extension we can
measure: calls that rang them, how many they answered and how fast, calls lost while ringing them,
outbound calls and connect rate, internal calls, transfers, missed calls they returned, talk time and
their working pattern. Bicom gets a lighter version (answered + outbound); TruSIP has no per-user data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from cdr_engine import Analysis, Settings, ext_label, fmt_dur, in_hours


@dataclass
class UserReport:
    available: bool
    level: str                      # "full" (Grandstream), "basic" (Bicom) or "none"
    table: pd.DataFrame             # one row per person
    hourly: dict                    # ext -> DataFrame(hour, inbound, outbound)
    observations: dict              # ext -> [str]
    team_observations: list
    team: dict                      # team-level numbers
    groups: dict = field(default_factory=dict)  # ring group -> [ext]
    note: str = ""
    group_table: pd.DataFrame = field(default_factory=pd.DataFrame)


def _empty(level: str, note: str) -> UserReport:
    return UserReport(False, level, pd.DataFrame(), {}, {}, [], {}, {}, note)


def _pct(a, b):
    return 100.0 * a / b if b else np.nan


def _clock(minutes):
    if minutes is None or (isinstance(minutes, float) and np.isnan(minutes)):
        return "-"
    minutes = int(round(minutes))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


SHARED_HINTS = ("meeting", "spare", "room", "office", "reception", "kitchen", "conference", "home")


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #
def build_user_report(df: pd.DataFrame, a: Analysis, start: date, end: date) -> UserReport:
    provider = a.provider
    u = df.attrs.get("users") if hasattr(df, "attrs") else None
    if provider == "grandstream" and u:
        return _grandstream(u, a, start, end)
    if provider == "bicom":
        return _basic(a)
    return _empty("none", "This phone system's export records calls per line, not per person, so a user "
                          "assessment isn't possible from it.")


def _window(t: pd.DataFrame, col: str, start: date, end: date) -> pd.DataFrame:
    if t is None or t.empty:
        return pd.DataFrame(columns=list(t.columns) if t is not None else [])
    lo, hi = pd.Timestamp(start), pd.Timestamp(end) + pd.Timedelta(days=1)
    return t[(t[col] >= lo) & (t[col] < hi)].copy()


def _grandstream(u: dict, a: Analysis, start: date, end: date) -> UserReport:
    s: Settings = a.settings
    names: dict = u["names"]
    ring = _window(u["ring"], "call_start", start, end)
    outb = _window(u["outbound"], "ts", start, end)
    internal = _window(u["internal"], "ts", start, end)
    transfers = _window(u["transfers"], "ts", start, end)

    if ring.empty and outb.empty:
        return _empty("full", "No user activity in this period.")

    # groups & membership
    groups: dict[str, list[str]] = {}
    if not ring.empty:
        for g, gg in ring[ring["group"] != "direct"].groupby("group"):
            groups[g] = sorted(gg["ext"].unique())

    # ---- inbound (ring events) ----
    if not ring.empty:
        ring["lost"] = ~ring["call_answered"] & (ring["window_s"] >= s.short_abandon_s)
        per_call = ring.groupby(["ext", "session"]).agg(
            answered=("answered", "max"), lost=("lost", "max"), ring_s=("ring_s", "min"),
            talk_s=("talk_s", "max"), ts=("call_start", "min"), declined=("declined", "max"))
        inb = per_call.groupby("ext").agg(
            rang=("answered", "size"), answered=("answered", "sum"), lost=("lost", "sum"),
            declined=("declined", "sum"), avg_ring_s=("ring_s", "mean"), in_talk_s=("talk_s", "sum"))
    else:
        per_call = pd.DataFrame(columns=["answered", "ts"])
        inb = pd.DataFrame(columns=["rang", "answered", "lost", "declined", "avg_ring_s", "in_talk_s"])

    # ---- outbound ----
    if not outb.empty:
        outb["connected"] = (outb["status"] == "ANSWERED") & (outb["talk_s"] > 0)
        out = outb.groupby("ext").agg(outbound=("ts", "size"), connected=("connected", "sum"),
                                      out_talk_s=("talk_s", "sum"), numbers=("dialled", "nunique"))
    else:
        out = pd.DataFrame(columns=["outbound", "connected", "out_talk_s", "numbers"])

    # ---- internal & transfers ----
    int_made = internal.groupby("from_ext").size() if not internal.empty else pd.Series(dtype=int)
    int_recv = internal[(internal["status"] == "ANSWERED") & (internal["talk_s"] > 0)].groupby("to_ext").size() \
        if not internal.empty else pd.Series(dtype=int)
    tr_out = transfers.groupby("from_ext").size() if not transfers.empty else pd.Series(dtype=int)

    # ---- missed calls returned (from the main analysis) ----
    m = a.missed
    returned = m[m["follow_up"] == "Called back - spoke"].groupby("follow_up_by").size() if len(m) else pd.Series(
        dtype=int)

    exts = sorted(set(inb.index) | set(out.index) | set(int_made.index))
    t = pd.DataFrame(index=pd.Index(exts, name="ext"))
    t["name"] = [names.get(e, f"Ext {e}") for e in exts]
    t = t.join(inb).join(out)
    t["internal_made"] = int_made.reindex(exts).fillna(0)
    t["internal_received"] = int_recv.reindex(exts).fillna(0)
    t["transfers_out"] = tr_out.reindex(exts).fillna(0)
    t["returned"] = [int(returned.get(n, 0)) for n in t["name"]]
    for c in ("rang", "answered", "lost", "declined", "in_talk_s", "outbound", "connected", "out_talk_s", "numbers",
              "internal_made", "internal_received", "transfers_out"):
        t[c] = pd.to_numeric(t[c], errors="coerce").fillna(0).astype(int)
    t["groups"] = [", ".join(g for g, members in groups.items() if e in members) for e in exts]
    t["pickup_pct"] = [_pct(r.answered, r.rang) for r in t.itertuples()]
    total_answered = int(t["answered"].sum())
    t["share_pct"] = [_pct(r.answered, total_answered) for r in t.itertuples()]
    t["connect_pct"] = [_pct(r.connected, r.outbound) for r in t.itertuples()]
    t["avg_in_talk_s"] = [r.in_talk_s / r.answered if r.answered else np.nan for r in t.itertuples()]
    t["avg_out_talk_s"] = [r.out_talk_s / r.connected if r.connected else np.nan for r in t.itertuples()]
    t["talk_s"] = t["in_talk_s"] + t["out_talk_s"]
    t["handled"] = t["answered"] + t["outbound"]

    # ---- working pattern & hourly profile ----
    acts = []
    if len(per_call):
        pa = per_call[per_call["answered"]].reset_index()[["ext", "ts"]].assign(kind="in")
        acts.append(pa)
    if not outb.empty:
        acts.append(outb[["ext", "ts"]].assign(kind="out"))
    if not internal.empty:
        acts.append(internal[["from_ext", "ts"]].rename(columns={"from_ext": "ext"}).assign(kind="int"))
    act = pd.concat(acts, ignore_index=True) if acts else pd.DataFrame(columns=["ext", "ts", "kind"])
    act = act[act["ext"].isin(exts)]
    hourly, first_l, last_l, days_l, ooh_l = {}, [], [], [], []
    for e in exts:
        ae = act[act["ext"] == e]
        h = ae[ae["kind"] != "int"].assign(hour=lambda x: x["ts"].dt.hour)
        hp = h.pivot_table(index="hour", columns="kind", values="ts", aggfunc="count", fill_value=0)
        hp = hp.reindex(range(24), fill_value=0)
        for col in ("in", "out"):
            if col not in hp:
                hp[col] = 0
        hourly[e] = hp[["in", "out"]].rename(columns={"in": "inbound", "out": "outbound"})
        if len(ae):
            daily = ae.assign(d=ae["ts"].dt.date).groupby("d")["ts"].agg(["min", "max"])
            first_l.append(float(np.median([x.hour * 60 + x.minute for x in daily["min"]])))
            last_l.append(float(np.median([x.hour * 60 + x.minute for x in daily["max"]])))
            days_l.append(len(daily))
            ooh_l.append(int((~in_hours(ae["ts"], s)).sum()))
        else:
            first_l.append(np.nan)
            last_l.append(np.nan)
            days_l.append(0)
            ooh_l.append(0)
    t["first_active"] = first_l
    t["last_active"] = last_l
    t["active_days"] = days_l
    t["out_of_hours"] = ooh_l
    t["per_day"] = [r.handled / r.active_days if r.active_days else 0 for r in t.itertuples()]

    t = t[(t["handled"] + t["rang"] + t["internal_made"]) > 0]
    t = t.sort_values(["handled", "rang"], ascending=False)

    team = {
        "people": int((t["handled"] > 0).sum()),
        "answered": total_answered,
        "outbound": int(t["outbound"].sum()),
        "connected": int(t["connected"].sum()),
        "avg_ring_s": float(per_call[per_call["answered"]]["ring_s"].mean()) if len(per_call) else np.nan,
        "avg_in_talk_s": float(t["in_talk_s"].sum() / total_answered) if total_answered else np.nan,
        "avg_out_talk_s": float(t["out_talk_s"].sum() / max(1, t["connected"].sum())),
        "connect_pct": _pct(t["connected"].sum(), t["outbound"].sum()),
        "returned": int(t["returned"].sum()),
        "internal": int(t["internal_made"].sum()),
        "transfers": int(t["transfers_out"].sum()),
        "talk_s": int(t["talk_s"].sum()),
        "top": t.sort_values("answered", ascending=False).iloc[0]["name"] if len(t) and total_answered else "-",
    }
    # ring-group performance
    gt = []
    if not ring.empty:
        rg = ring[ring["group"] != "direct"]
        for g, gg in rg.groupby("group"):
            calls = gg.groupby("session").agg(ans=("answered", "max"), lost=("lost", "max"))
            ans_by = gg[gg["answered"]].groupby("ext").size().sort_values(ascending=False)
            top_e = ans_by.index[0] if len(ans_by) else None
            gt.append({"group": g, "members": gg["ext"].nunique(), "calls": len(calls),
                       "answered": int(calls["ans"].sum()), "lost": int(calls["lost"].sum()),
                       "answer_pct": _pct(calls["ans"].sum(), len(calls)),
                       "answering_people": int((ans_by > 0).sum()),
                       "top": names.get(top_e, top_e) if top_e else "-",
                       "top_pct": _pct(ans_by.iloc[0], ans_by.sum()) if len(ans_by) else np.nan,
                       "avg_pickup": float(t.loc[t.index.isin(gg["ext"].unique()) & (t["rang"] >= 40),
                                                 "pickup_pct"].mean())})
    group_table = pd.DataFrame(gt)
    obs = {e: _user_observations(r, t, team, groups, hourly.get(e), group_table) for e, r in t.iterrows()}
    team_obs = _team_observations(t, team, groups, a)
    return UserReport(True, "full", t, hourly, obs, team_obs, team, groups, group_table=group_table)


def _basic(a: Analysis) -> UserReport:
    """Bicom: who answered and who dialled out - no ring-by-ring detail."""
    ans = a.inbound[a.inbound["outcome"].isin(["Answered", "Answered via overflow"])]
    answered = ans[ans["answered_by"] != ""].groupby("answered_by").agg(
        answered=("call_id", "count"), in_talk_s=("talk_s", "sum"), avg_ring_s=("ring_s", "mean"))
    team = a.team
    names = sorted(set(answered.index) | set(team.index))
    if not names:
        return _empty("basic", "No per-extension activity found in this period.")
    t = pd.DataFrame(index=pd.Index(names, name="ext"))
    t["name"] = names
    t = t.join(answered)
    t["outbound"] = team["outbound"].reindex(names) if len(team) else 0
    t["connected"] = team["connected"].reindex(names) if len(team) else 0
    t["out_talk_s"] = team["talk_s"].reindex(names) if len(team) else 0
    t["returned"] = team["missed_calls_returned"].reindex(names) if len(team) else 0
    for c in ("answered", "in_talk_s", "outbound", "connected", "out_talk_s", "returned"):
        t[c] = pd.to_numeric(t[c], errors="coerce").fillna(0).astype(int)
    for c in ("rang", "lost", "declined", "numbers", "internal_made", "internal_received", "transfers_out",
              "active_days", "out_of_hours"):
        t[c] = 0
    total_answered = int(t["answered"].sum())
    t["groups"] = ""
    t["pickup_pct"] = np.nan
    t["share_pct"] = [_pct(r.answered, total_answered) for r in t.itertuples()]
    t["connect_pct"] = [_pct(r.connected, r.outbound) for r in t.itertuples()]
    t["avg_in_talk_s"] = [r.in_talk_s / r.answered if r.answered else np.nan for r in t.itertuples()]
    t["avg_out_talk_s"] = [r.out_talk_s / r.connected if r.connected else np.nan for r in t.itertuples()]
    t["talk_s"] = t["in_talk_s"] + t["out_talk_s"]
    t["handled"] = t["answered"] + t["outbound"]
    t["first_active"] = np.nan
    t["last_active"] = np.nan
    t["per_day"] = t["handled"] / max(1, a.kpis["open_days"])
    t = t.sort_values("handled", ascending=False)
    hourly = {}
    for e in t.index:
        hi = ans[ans["answered_by"] == e]["ts"].dt.hour.value_counts()
        ho = a.outbound[a.outbound["agent"] == e]["ts"].dt.hour.value_counts()
        hourly[e] = pd.DataFrame({"inbound": hi.reindex(range(24), fill_value=0),
                                  "outbound": ho.reindex(range(24), fill_value=0)})
    team_d = {
        "people": int((t["handled"] > 0).sum()), "answered": total_answered, "outbound": int(t["outbound"].sum()),
        "connected": int(t["connected"].sum()), "avg_ring_s": a.kpis["avg_ring_s"],
        "avg_in_talk_s": float(t["in_talk_s"].sum() / total_answered) if total_answered else np.nan,
        "avg_out_talk_s": float(t["out_talk_s"].sum() / max(1, t["connected"].sum())),
        "connect_pct": _pct(t["connected"].sum(), t["outbound"].sum()), "returned": int(t["returned"].sum()),
        "internal": 0, "transfers": 0, "talk_s": int(t["talk_s"].sum()),
        "top": t.sort_values("answered", ascending=False).iloc[0]["name"] if total_answered else "-",
    }
    obs = {e: _user_observations(r, t, team_d, {}, hourly.get(e)) for e, r in t.iterrows()}
    return UserReport(True, "basic", t, hourly, obs, _team_observations(t, team_d, {}, a), team_d, {},
                      "Bicom exports show who answered and who dialled out, but not every handset that rang, so "
                      "pick-up rate, calls lost while ringing and working hours aren't available.")


# --------------------------------------------------------------------------- #
# Plain-English observations
# --------------------------------------------------------------------------- #
def _user_observations(r, t: pd.DataFrame, team: dict, groups: dict, hp=None,
                       group_table: pd.DataFrame | None = None) -> list[str]:
    o: list[str] = []
    name_l = str(r["name"]).lower()
    shared = any(h in name_l for h in SHARED_HINTS)
    answered, rang, outbound = int(r["answered"]), int(r["rang"]), int(r["outbound"])
    total_ans = max(1, team["answered"])

    # role / workload
    if answered and r["name"] == team["top"]:
        o.append(f"Answered the most inbound calls on the team: {answered:,} ({r['share_pct']:.0f}% of all "
                 f"answered calls).")
    elif answered >= 0.1 * total_ans:
        o.append(f"A key call handler, answering {answered:,} inbound calls ({r['share_pct']:.0f}% of the team's).")

    # rings but rarely answers
    if rang >= 40 and (answered / rang if rang else 0) < 0.05:
        grp = f" ring group {r['groups']}" if r["groups"] else " a ring group"
        o.append(f"Rang on {rang:,} inbound calls but answered {answered:,}. If answering isn't part of their role, "
                 f"remove them from{grp}; if it is, check the handset isn't on Do Not Disturb or logged out.")
    elif rang >= 40 and r["pickup_pct"] < 10 and answered:
        o.append(f"Picked up {r['pickup_pct']:.0f}% of the calls that rang them ({answered:,} of {rang:,}).")

    # pick-up rate vs their ring group(s)
    if rang >= 40 and answered >= 10 and group_table is not None and len(group_table) and r["groups"]:
        gs_ = [g for g in str(r["groups"]).split(", ") if g]
        avg = group_table[group_table["group"].isin(gs_)]["avg_pickup"].mean()
        if avg == avg and r["pickup_pct"] >= avg * 1.4:
            o.append(f"Picks up {r['pickup_pct']:.0f}% of the calls that ring them - well above the "
                     f"{avg:.0f}% average for their ring group.")

    # answer speed vs team
    team_ring = team.get("avg_ring_s")
    if answered >= 10 and team_ring and not np.isnan(team_ring) and not np.isnan(r.get("avg_ring_s", np.nan)):
        if r["avg_ring_s"] <= team_ring * 0.7:
            o.append(f"Quick to answer: {fmt_dur(r['avg_ring_s'])} on average against a team average of "
                     f"{fmt_dur(team_ring)}.")
        elif r["avg_ring_s"] >= team_ring * 1.4 and r["avg_ring_s"] >= 8:
            o.append(f"Slower to answer than the team: {fmt_dur(r['avg_ring_s'])} on average (team "
                     f"{fmt_dur(team_ring)}).")

    # outbound
    if outbound >= 20 and outbound >= 2 * max(1, answered):
        to = f" to {int(r['numbers']):,} different numbers" if r["numbers"] else ""
        o.append(f"Mainly makes outbound calls: {outbound:,} calls{to}, {fmt_dur(r['out_talk_s'])} on the phone.")
    if outbound >= 20 and not np.isnan(r["connect_pct"]) and team.get("connect_pct") and \
            r["connect_pct"] < team["connect_pct"] - 12:
        o.append(f"Only {r['connect_pct']:.0f}% of outbound calls connected (team {team['connect_pct']:.0f}%) - "
                 f"worth checking call times or the numbers being dialled.")

    # call length
    tav = team.get("avg_in_talk_s")
    if answered >= 15 and tav and not np.isnan(r["avg_in_talk_s"]):
        if r["avg_in_talk_s"] >= tav * 1.6:
            o.append(f"Long inbound calls: {fmt_dur(r['avg_in_talk_s'])} on average (team {fmt_dur(tav)}).")
        elif r["avg_in_talk_s"] <= tav * 0.5:
            o.append(f"Short inbound calls: {fmt_dur(r['avg_in_talk_s'])} on average (team {fmt_dur(tav)}) - "
                     f"possibly triaging or passing calls on.")

    # transfers
    if r["transfers_out"] >= 5 and answered and r["transfers_out"] / answered >= 0.1:
        o.append(f"Transferred {int(r['transfers_out'])} calls to colleagues ({100 * r['transfers_out'] / answered:.0f}% "
                 f"of the calls they answered) - some callers may be better routed straight to the right person.")

    # missed calls returned
    if r["returned"] >= 3:
        o.append(f"Returned {int(r['returned'])} missed calls - good follow-up.")

    # working pattern
    if r.get("active_days", 0) >= 3 and not np.isnan(r.get("first_active", np.nan)):
        o.append(f"Typically on the phone {_clock(r['first_active'])}-{_clock(r['last_active'])}, across "
                 f"{int(r['active_days'])} days ({r['per_day']:.0f} calls handled per active day).")
    if r.get("out_of_hours", 0) >= 5:
        o.append(f"{int(r['out_of_hours'])} calls handled outside the opening hours set for this report.")

    # busiest hour
    if hp is not None and hp.values.sum() >= 20:
        tot = hp.sum(axis=1)
        bh = int(tot.idxmax())
        o.append(f"Busiest hour {bh:02d}:00-{bh + 1:02d}:00 ({int(tot.max())} calls in the period).")

    # low activity / shared device
    if r["handled"] < 10 and not (rang >= 40 and answered == 0):
        o.append("Very little phone activity in this period" +
                 (" - looks like a shared or spare handset." if shared else "."))
    if not o:
        o.append(f"{answered:,} inbound answered and {outbound:,} outbound calls in the period.")
    return o[:6]


def _team_observations(t: pd.DataFrame, team: dict, groups: dict, a: Analysis) -> list[str]:
    o: list[str] = []
    if team["answered"]:
        top3 = t.sort_values("answered", ascending=False).head(3)
        share = 100 * top3["answered"].sum() / team["answered"]
        o.append(f"{len(top3)} people ({', '.join(top3['name'])}) answered {share:.0f}% of all inbound calls."
                 + (" Cover for these people is critical when they're away." if share >= 50 else ""))
    silent = t[(t["rang"] >= 40) & (t["answered"] < 0.05 * t["rang"].clip(lower=1))]
    if len(silent):
        who = ", ".join(silent["name"].head(5)) + (f" and {len(silent) - 5} more" if len(silent) > 5 else "")
        o.append(f"{len(silent)} ring-group member{'s' if len(silent) > 1 else ''} ({who}) "
                 f"rang on hundreds of calls but rarely answered - review ring-group membership so calls reach "
                 f"people who will pick up.")
    for g, members in groups.items():
        sub = t[t.index.isin(members)]
        if sub["answered"].sum() >= 20:
            lead = sub.sort_values("answered", ascending=False).iloc[0]
            pct = 100 * lead["answered"] / max(1, sub["answered"].sum())
            if pct >= 35:
                o.append(f"Ring group {g}: {lead['name']} answers {pct:.0f}% of the group's calls.")
    if team["outbound"]:
        o.append(f"The team made {team['outbound']:,} outbound calls; {team['connect_pct']:.0f}% connected.")
    if team["returned"]:
        o.append(f"{team['returned']} missed calls were returned by named team members; "
                 f"{a.kpis['unreturned_callers']} callers still have not been spoken to.")
    if team.get("transfers"):
        o.append(f"{team['transfers']} calls were transferred between colleagues.")
    return o


def user_table_view(rep: UserReport) -> pd.DataFrame:
    """Friendly table for the app / CSV download."""
    t = rep.table
    if t.empty:
        return t
    v = pd.DataFrame({
        "Name": t["name"],
        "Ext": t.index,
        "Ring group(s)": t["groups"],
        "Rang them": t["rang"],
        "Answered": t["answered"],
        "Pick-up %": t["pickup_pct"].round(0),
        "Share of team %": t["share_pct"].round(1),
        "Avg time to answer": t["avg_ring_s"].map(fmt_dur) if "avg_ring_s" in t else "-",
        "Lost while ringing": t["lost"],
        "Outbound": t["outbound"],
        "Connected %": t["connect_pct"].round(0),
        "Talk time": t["talk_s"].map(fmt_dur),
        "Internal calls": t["internal_made"],
        "Transfers": t["transfers_out"],
        "Missed calls returned": t["returned"],
        "Typical hours": [f"{_clock(r.first_active)}-{_clock(r.last_active)}" if r.first_active == r.first_active
                          else "-" for r in t.itertuples()],
    })
    if rep.level == "basic":
        v = v.drop(columns=["Ext", "Ring group(s)", "Rang them", "Pick-up %", "Lost while ringing", "Internal calls",
                            "Transfers", "Typical hours"])
    return v.reset_index(drop=True)
