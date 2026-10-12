"""
ui.py - shared look, helpers and the results dashboard used by both the upload app and customer portals.
"""
from __future__ import annotations

import html as html_lib
import inspect

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import cdr_engine as ce
import users as us
from report_pdf import build_pdf, build_user_pdf

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
:root{--bg:#0A0E1A;--surface:#111827;--surface-2:#161F33;--surface-3:#1C2740;--border:rgba(148,163,184,.14);--border-strong:rgba(148,163,184,.26);--text:#E7EAF3;--muted:#8C98B0;--faint:#5E6A82;--accent:#7C83FF;--accent-2:#38D6F5;--accent-soft:rgba(124,131,255,.14);--good:#34D399;--warn:#FBBF24;--risk:#FB923C;--bad:#F87171;--radius:14px;--grad:linear-gradient(135deg,#7C83FF 0%,#38D6F5 100%);}
html,body,[class*="css"],.stApp,button,input,textarea,select{font-family:'Inter',system-ui,-apple-system,'Segoe UI',sans-serif!important}
.stApp{background:radial-gradient(1200px 500px at 85% -10%,rgba(56,214,245,.07),transparent 60%),radial-gradient(900px 500px at 10% -20%,rgba(124,131,255,.10),transparent 60%),var(--bg)}
[data-testid="stHeader"]{background:transparent}[data-testid="stDecoration"]{display:none}footer{visibility:hidden}
.block-container{padding-top:1.6rem!important;padding-bottom:3rem!important;max-width:1500px}
[data-testid="stSidebar"]{background:linear-gradient(180deg,#0D1322 0%,#0A0E1A 100%);border-right:1px solid var(--border)}
[data-testid="stWidgetLabel"] p{font-size:.76rem!important;font-weight:600!important;color:var(--muted)!important;text-transform:uppercase;letter-spacing:.06em}
[data-testid="stCaptionContainer"]{color:var(--muted)!important}
.st-key-card-upload,.st-key-card-period,.st-key-card-results,.st-key-card-report,.st-key-card-login,.st-key-card-source{background:linear-gradient(180deg,rgba(22,31,51,.85) 0%,rgba(17,24,39,.85) 100%);border:1px solid var(--border)!important;border-radius:var(--radius);padding:22px 22px 18px;box-shadow:0 1px 0 rgba(255,255,255,.03) inset,0 20px 40px -24px rgba(0,0,0,.6);margin-bottom:18px}
[data-baseweb="input"],[data-baseweb="select"]>div,[data-baseweb="textarea"]{background:var(--surface)!important;border:1px solid var(--border-strong)!important;border-radius:10px!important}
[data-baseweb="input"]:focus-within,[data-baseweb="select"]>div:focus-within,[data-baseweb="textarea"]:focus-within{border-color:var(--accent)!important;box-shadow:0 0 0 3px var(--accent-soft)!important}
[data-baseweb="input"]>div,[data-baseweb="base-input"]{background:transparent!important}
.stButton button,.stDownloadButton button,.stFormSubmitButton button{border-radius:10px!important;font-weight:600!important;border:1px solid var(--border-strong)!important;background:var(--surface-2)!important;color:var(--text)!important;transition:all .15s ease}
.stButton button:hover,.stDownloadButton button:hover{border-color:var(--accent)!important;transform:translateY(-1px)}
.stButton button[kind="primary"],.stDownloadButton button[kind="primary"],[data-testid="stBaseButton-primary"],[data-testid="stBaseButton-primaryFormSubmit"],.stFormSubmitButton button[kind="primaryFormSubmit"]{background:var(--grad)!important;border:none!important;color:#0A0E1A!important;box-shadow:0 8px 24px -10px rgba(124,131,255,.8)}
.stButton button[kind="primary"] p,.stDownloadButton button[kind="primary"] p,[data-testid="stBaseButton-primary"] p,[data-testid="stBaseButton-primaryFormSubmit"] p,.stFormSubmitButton button[kind="primaryFormSubmit"] p{color:#0A0E1A!important;font-weight:700!important}
[data-testid="stTabs"] [role="tablist"],[data-baseweb="tab-list"]{gap:4px;background:var(--surface);padding:4px;border-radius:12px;border:1px solid var(--border);width:fit-content;max-width:100%;overflow-x:auto}
[data-testid="stTabs"] [role="tab"],[data-baseweb="tab"]{border-radius:9px!important;padding:8px 16px!important;color:var(--muted)!important;background:transparent!important}
[data-testid="stTabs"] [role="tab"][aria-selected="true"],[data-baseweb="tab"][aria-selected="true"]{background:var(--surface-3)!important;color:var(--text)!important}
[data-baseweb="tab-highlight"],[data-baseweb="tab-border"],[data-testid="stTabs"] .react-aria-SelectionIndicator{display:none!important}
[data-testid="stDataFrame"]{border:1px solid var(--border);border-radius:12px;overflow:hidden}
[data-testid="stExpander"] details{background:var(--surface);border:1px solid var(--border)!important;border-radius:12px!important}
[data-testid="stFileUploaderDropzone"]{background:var(--surface)!important;border:1.5px dashed var(--border-strong)!important;border-radius:12px!important}
.pe-hero{display:flex;align-items:center;justify-content:space-between;gap:24px;flex-wrap:wrap;padding:6px 2px 22px;margin-bottom:18px;border-bottom:1px solid var(--border)}
.pe-eyebrow{display:inline-flex;align-items:center;gap:8px;font-size:.72rem;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--accent-2);margin-bottom:8px}
.pe-eyebrow .dot{width:7px;height:7px;border-radius:50%;background:var(--good);box-shadow:0 0 0 4px rgba(52,211,153,.15)}
.pe-title{font-size:2.05rem;font-weight:800;letter-spacing:-.035em;line-height:1.1;color:var(--text)}
.pe-title span{background:var(--grad);-webkit-background-clip:text;background-clip:text;color:transparent}
.pe-sub{color:var(--muted);font-size:.95rem;margin-top:8px;max-width:640px}
.pe-stepper{display:flex;align-items:center;gap:6px;background:var(--surface);border:1px solid var(--border);border-radius:999px;padding:6px;flex-wrap:wrap}
.pe-step{display:flex;align-items:center;gap:8px;padding:7px 14px 7px 7px;border-radius:999px;font-size:.82rem;font-weight:600;color:var(--faint);white-space:nowrap}
.pe-step .num{width:24px;height:24px;border-radius:50%;display:grid;place-items:center;font-size:.72rem;font-weight:700;border:1px solid var(--border-strong)}
.pe-step.done{color:var(--muted)}.pe-step.done .num{background:rgba(52,211,153,.14);border-color:rgba(52,211,153,.45);color:var(--good)}
.pe-step.active{background:var(--surface-3);color:var(--text)}.pe-step.active .num{background:var(--grad);border:none;color:#0A0E1A}
.pe-step-sep{width:14px;height:1px;background:var(--border-strong)}
.pe-section{display:flex;align-items:center;gap:12px;margin-bottom:16px}
.pe-section .badge{width:34px;height:34px;border-radius:10px;display:grid;place-items:center;background:var(--accent-soft);color:var(--accent);font-weight:800;font-size:.85rem;border:1px solid rgba(124,131,255,.3)}
.pe-section .t{font-size:1.08rem;font-weight:700;color:var(--text)}.pe-section .s{font-size:.82rem;color:var(--muted);margin-top:2px}
.pe-chip{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:999px;font-size:.76rem;font-weight:600;background:var(--surface-3);color:var(--text);border:1px solid var(--border);white-space:nowrap;margin:0 6px 6px 0}
.pe-chip.accent{background:var(--accent-soft);color:#B9BDFF;border-color:rgba(124,131,255,.3)}
.pe-chip.good{background:rgba(52,211,153,.12);color:var(--good);border-color:rgba(52,211,153,.3)}
.pe-chip.warn{background:rgba(251,191,36,.12);color:var(--warn);border-color:rgba(251,191,36,.3)}
.pe-chip.bad{background:rgba(248,113,113,.12);color:var(--bad);border-color:rgba(248,113,113,.3)}
.pe-logo{width:40px;height:40px;border-radius:12px;background:var(--grad);display:grid;place-items:center;color:#0A0E1A;box-shadow:0 10px 24px -10px rgba(124,131,255,.9);flex:none}
.pe-brand{display:flex;align-items:center;gap:12px;padding:4px 0 14px;border-bottom:1px solid var(--border);margin-bottom:14px}
.pe-brand .n{font-weight:800;color:var(--text);letter-spacing:-.02em}.pe-brand .tg{font-size:.78rem;color:var(--muted)}
.pe-kpis{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px;margin-bottom:18px}
@media(max-width:1100px){.pe-kpis{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(max-width:640px){.pe-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}}
.pe-kpi{background:var(--surface);border:1px solid var(--border);border-top:2px solid var(--accent);border-radius:14px;padding:16px 18px}
.pe-kpi.good{border-top-color:var(--good)}.pe-kpi.warn{border-top-color:var(--warn)}.pe-kpi.bad{border-top-color:var(--bad)}.pe-kpi.cyan{border-top-color:var(--accent-2)}
.pe-kpi .l{font-size:.7rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.pe-kpi .v{font-size:1.75rem;font-weight:800;letter-spacing:-.03em;color:var(--text);margin-top:6px}
.pe-kpi .s{font-size:.78rem;color:var(--muted);margin-top:2px}
.pe-empty{display:flex;gap:18px;align-items:flex-start;padding:6px 2px}
.pe-empty .ic{width:52px;height:52px;border-radius:14px;background:var(--accent-soft);color:var(--accent);display:grid;place-items:center;flex:none;border:1px solid rgba(124,131,255,.3)}
.pe-empty .t{font-weight:700;color:var(--text);font-size:1rem}
.pe-empty ol{color:var(--muted);font-size:.88rem;margin:8px 0 0 18px;padding:0}
.pe-summary{background:var(--surface);border:1px solid var(--border);border-left:3px solid var(--accent-2);border-radius:12px;padding:16px 18px;color:var(--text);line-height:1.6;font-size:.95rem;margin-bottom:16px}
.pe-list{margin:0;padding:0;list-style:none}
.pe-list li{display:flex;gap:10px;padding:10px 0;border-bottom:1px solid var(--border);color:var(--text);font-size:.9rem;line-height:1.5}
.pe-list li:last-child{border-bottom:none}
.pe-list .b{flex:none;width:22px;height:22px;border-radius:7px;display:grid;place-items:center;font-size:.72rem;font-weight:800;background:var(--accent-soft);color:var(--accent);margin-top:1px}
.pe-list.rec .b{background:rgba(52,211,153,.14);color:var(--good)}
.pe-h{font-size:.78rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin:4px 0 8px}
"""


def inject_css():
    st.markdown(f"<style>{CSS}</style>", unsafe_allow_html=True)


def esc(v):
    return html_lib.escape(str(v if v is not None else ""), quote=True)


def render_html(markup, target=None):
    (target or st).markdown("".join(l.strip() for l in markup.splitlines()), unsafe_allow_html=True)


def chip(text, tone=""):
    return f'<span class="pe-chip {tone}">{esc(text)}</span>'


def section_header(num, title, subtitle=""):
    render_html(f'<div class="pe-section"><div class="badge">{num}</div><div><div class="t">{esc(title)}</div>'
                + (f'<div class="s">{esc(subtitle)}</div>' if subtitle else "") + "</div></div>")


def _full_width():
    try:
        if "width" in inspect.signature(st.button).parameters:
            return {"width": "stretch"}
    except (TypeError, ValueError):
        pass
    return {"use_container_width": True}


FULL_WIDTH = _full_width()


def show_df(df, **kw):
    try:
        st.dataframe(df, width="stretch", hide_index=True, **kw)
    except TypeError:
        st.dataframe(df, use_container_width=True, hide_index=True, **kw)


def show_chart(fig):
    try:
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    except TypeError:
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


ICON_PHONE = ('<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
              'stroke-linecap="round" stroke-linejoin="round"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 '
              '1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96'
              '.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.34 1.85.57 '
              '2.81.7A2 2 0 0 1 22 16.92z"/></svg>')
ICON_UPLOAD = ('<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
               'stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
               '<polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>')

# --------------------------------------------------------------------------- #
# Cached work
# --------------------------------------------------------------------------- #


@st.cache_data(show_spinner=False)
def load_files(blobs: tuple[bytes, ...], provider: str) -> pd.DataFrame:
    return ce.load_many(list(blobs), provider)


@st.cache_data(show_spinner=False)
def run_analysis(df: pd.DataFrame, start, end, settings_tuple):
    s = ce.Settings(*settings_tuple)
    return ce.analyse(df, start, end, s)


@st.cache_data(show_spinner=False)
def make_pdf(_a, key, customer, brand, accent, logo, style):
    return build_pdf(_a, customer, brand, accent, logo, style)


@st.cache_data(show_spinner=False)
def make_user_report(df, _a, key, start, end):
    return us.build_user_report(df, _a, start, end)


@st.cache_data(show_spinner=False)
def make_user_pdf(_a, _rep, key, customer, brand, accent, logo, style):
    return build_user_pdf(_a, _rep, customer, brand, accent, logo, style)



def plot_layout(fig, h=340):
    fig.update_layout(height=h, margin=dict(l=10, r=10, t=78, b=10), paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", font=dict(family="Inter", color="#C7CEDD", size=12),
                      legend=dict(orientation="h", y=1.0, yanchor="bottom", x=0, traceorder="normal"), barmode="stack", hovermode="x unified")
    fig.update_xaxes(gridcolor="rgba(148,163,184,.08)", zeroline=False)
    fig.update_yaxes(gridcolor="rgba(148,163,184,.10)", zeroline=False)
    return fig



def render_results(df, a, p_start, p_end, settings_tuple, customer, brand, accent, logo_file, pdf_style,
                   portal=False, first_num=3):
    """Cards 03 (results tabs) and 04 (downloads). Returns the stepper stage reached."""
    stage = 3
    if a is not None and a.kpis["inbound_total"] == 0:
        with st.container(key="card-results"):
            st.info("No inbound calls in this period.")
    elif a is not None:
        k, s = a.kpis, a.settings
        with st.container(key="card-results"):
            section_header(f"{first_num:02d}", "Call handling at a glance", f"{a.period_start:%d %b %Y} → {a.period_end:%d %b %Y}")
            ar = k["answer_rate_ih"]
            tone = "good" if ar >= s.answer_target_pct else ("warn" if ar >= s.answer_target_pct - 10 else "bad")
            routing, ring = a.caps["routing"], a.caps["ring_time"]
            tiles = [
                ("Inbound calls", f"{k['inbound_total']:,}", f"{k['avg_inbound_per_open_day']:.0f} per working day", ""),
                ("Answered in hours", f"{ar:.1f}%", f"target {s.answer_target_pct:.0f}%", tone),
                ("Truly missed", f"{k['true_missed']:,}", f"PBX log said {k['pbx_reported_missed']:,}", "bad")
                if routing else ("Missed calls", f"{k['true_missed']:,}",
                                 f"{100 - k['answer_rate']:.1f}% of all inbound", "bad"),
                ("Saved by overflow", f"{k['answered_overflow']:,}",
                 f"{100 * k['answered_overflow'] / max(1, k['answered']):.0f}% of answered", "cyan")
                if routing else ("Engaged / busy", f"{k['busy']:,}", "callers who got a busy tone",
                                 "warn" if k["busy"] else "good"),
                ("Un-returned callers", f"{k['unreturned_callers']:,}", f"{k['unreturned_calls']:,} missed calls",
                 "bad" if k["unreturned_callers"] else "good"),
                ("Avg time to answer", ce.fmt_dur(k["avg_ring_s"]), f"median call-back {ce.fmt_mins(k['median_callback_min'])}", "")
                    if ring else ("Avg call length", ce.fmt_dur(k["avg_talk_s"]),
                              f"median call-back {ce.fmt_mins(k['median_callback_min'])}", ""),
            ]
            render_html('<div class="pe-kpis">' + "".join(
                f'<div class="pe-kpi {t}"><div class="l">{esc(l)}</div><div class="v">{esc(v)}</div>'
                f'<div class="s">{esc(sub)}</div></div>' for l, v, sub, t in tiles) + "</div>")

            data_id = (a.provider, len(a.inbound), len(a.outbound), str(a.inbound["call_id"].head(20).tolist()))
            urep = make_user_report(df, a, (data_id, settings_tuple), p_start, p_end)
            tabs = st.tabs(["Overview", "Missed calls", "Un-returned callers", "Lines & team", "Insights",
                            "User assessment"])

            with tabs[0]:
                render_html(f'<div class="pe-summary">{esc(a.summary)}</div>')
                inb = a.inbound[~a.inbound["outcome"].isin(["Short abandon", "Failed"])]
                hrs = inb.assign(h=inb["ts"].dt.hour).pivot_table(index="h", columns="outcome", values="call_id",
                                                                  aggfunc="count", fill_value=0)
                hrs = hrs.reindex(range(24), fill_value=0)
                tot = hrs.sum(axis=1)
                keep = tot[tot >= max(1, tot.sum() * 0.003)].index
                hrs = hrs.loc[min(keep.min(), 8):max(keep.max(), 17)] if len(keep) else hrs.loc[8:17]
                fig = go.Figure()
                for col, colr in [("Answered", "#7C83FF"), ("Answered via overflow", "#38D6F5"),
                                  ("Missed", "#F87171"), ("Busy", "#FB923C"), ("Voicemail", "#FBBF24")]:
                    if col in hrs:
                        fig.add_bar(x=[f"{h:02d}:00" for h in hrs.index], y=(hrs[col] / k["days"]).round(2),
                                    name="Engaged (busy)" if col == "Busy" else col, marker_color=colr)
                plot_layout(fig).update_layout(title=dict(text="Average calls per day by hour", font=dict(size=14)))
                c1, c2 = st.columns([1.5, 1])
                with c1:
                    show_chart(fig)
                with c2:
                    d = a.daily.reset_index()
                    f2 = go.Figure()
                    f2.add_bar(x=d["day"], y=d["answered"], name="Answered", marker_color="#7C83FF")
                    f2.add_bar(x=d["day"], y=d["missed"], name="Missed", marker_color="#F87171")
                    plot_layout(f2).update_layout(title=dict(text="Daily volume", font=dict(size=14)))
                    show_chart(f2)
                heat = a.heat
                hcols = [h for h in heat.columns if heat[h].sum() >= max(1, heat.values.sum() * 0.003)] or list(range(8, 18))
                heat = heat.loc[:, min(hcols):max(hcols)]
                weeks = max(1, k["days"] / 7)
                f3 = go.Figure(go.Heatmap(z=(heat.values / weeks).round(1), x=[f"{h:02d}" for h in heat.columns],
                                          y=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], colorscale=[
                                              [0, "#111827"], [0.5, "#4F55C9"], [1, "#38D6F5"]], showscale=False,
                                          hovertemplate="%{y} %{x}:00 - %{z} calls/week<extra></extra>"))
                plot_layout(f3, 280).update_layout(title=dict(text="Busiest times (average calls per week)",
                                                              font=dict(size=14)), hovermode="closest")
                f3.update_yaxes(autorange="reversed")
                show_chart(f3)

            with tabs[1]:
                m = a.missed.copy()
                render_html(chip(f"{len(m):,} {'truly ' if routing else ''}missed", "bad")
                            + (chip(f"{k['voicemail']:,} to voicemail", "warn") if routing else "")
                            + (chip(f"{k['busy']:,} engaged", "warn") if k["busy"] else "")
                            + chip(f"{k['missed_resolved']:,} resolved", "good")
                            + (chip(f"{k['short_abandons']:,} quick hang-ups excluded") if ring else ""))
                opts = sorted(m["follow_up"].unique().tolist())
                pick = st.multiselect("Show follow-up status", opts, default=opts)
                m = m[m["follow_up"].isin(pick)]
                view = pd.DataFrame({
                    "When": m["ts"].dt.strftime("%a %d %b %H:%M"),
                    "Caller": m["caller"].map(ce.pretty_number),
                    "Line": m["line_name"],
                    "Outcome": m["outcome"] + m["detail"].map(lambda d: f" ({d})" if d else ""),
                    "In hours": m["in_hours"].map({True: "Yes", False: "No"}),
                    "Follow-up": m["follow_up"],
                    "Followed up": m["follow_up_ts"].dt.strftime("%d %b %H:%M").fillna(""),
                    "After": m["minutes_to_follow_up"].map(ce.fmt_mins),
                    "By": m["follow_up_by"],
                    "Route through PBX": m["route"],
                }).iloc[::-1]
                if not routing:
                    view = view.drop(columns=["Route through PBX"])
                    view["Outcome"] = view["Outcome"].replace({"Busy": "Engaged (busy)"})
                show_df(view, height=440)
                st.download_button("Download missed calls (CSV)", view.to_csv(index=False).encode(),
                                   "missed_calls.csv", "text/csv")

            with tabs[2]:
                u = a.unreturned_callers
                st.caption("Callers whose most recent missed call was never followed by a connected call-back or a "
                           "later answered call from them. This is the list to work through.")
                uv = pd.DataFrame({
                    "Caller": u["caller"].map(ce.pretty_number),
                    "Missed calls": u["missed_calls"],
                    "First missed": u["first_missed"].dt.strftime("%d %b %H:%M"),
                    "Last missed": u["last_missed"].dt.strftime("%d %b %H:%M"),
                    "Line": u["line_name"],
                    "Status": u["follow_up"],
                })
                show_df(uv, height=440)
                st.download_button("Download call-back list (CSV)", uv.to_csv(index=False).encode(),
                                   "unreturned_callers.csv", "text/csv")

            with tabs[3]:
                st.markdown('<div class="pe-h">Lines & branches</div>', unsafe_allow_html=True)
                lv = a.lines.reset_index().rename(columns={
                    "line_name": "Line / branch", "calls": "Calls", "answered": "Answered", "overflow": "Via overflow",
                    "busy": "Engaged",
                    "missed": "Missed", "answer_rate": "Answer %", "unreturned_callers": "Un-returned callers"})
                lv = lv.drop(columns=["Engaged"] if routing else ["Via overflow"])
                lv["Answer %"] = lv["Answer %"].round(1)
                show_df(lv, column_config={"Answer %": st.column_config.ProgressColumn(
                    "Answer %", min_value=0, max_value=100, format="%.0f%%")})
                st.markdown('<div class="pe-h">Team - outbound & call-backs</div>', unsafe_allow_html=True)
                tv = a.team.reset_index().rename(columns={
                    "agent": "Person / extension" if routing else "Outgoing line", "outbound": "Outbound calls", "connected": "Connected",
                    "talk_s": "Talk time", "missed_calls_returned": "Missed calls returned"})
                tv["Talk time"] = tv["Talk time"].map(ce.fmt_dur)
                ans = a.inbound[a.inbound["outcome"].isin(["Answered", "Answered via overflow"])]
                ans_by = ans[ans["answered_by"] != ""].groupby("answered_by").size()
                if len(ans_by):
                    first = tv.columns[0]
                    tv = tv.merge(ans_by.rename("Inbound answered").reset_index().rename(columns={"answered_by": first}),
                                  on=first, how="outer").fillna(0)
                    for col in ("Outbound calls", "Connected", "Missed calls returned", "Inbound answered"):
                        tv[col] = tv[col].astype(int)
                    tv["Talk time"] = tv["Talk time"].replace(0, "-")
                    tv = tv[[first, "Inbound answered", "Outbound calls", "Connected", "Talk time", "Missed calls returned"]]
                    tv = tv.sort_values(["Inbound answered", "Outbound calls"], ascending=False)
                show_df(tv)

            with tabs[4]:
                c1, c2 = st.columns(2)
                with c1:
                    st.markdown('<div class="pe-h">Observations</div>', unsafe_allow_html=True)
                    render_html('<ul class="pe-list">' + "".join(
                        f'<li><span class="b">•</span><span>{esc(o)}</span></li>' for o in a.observations) + "</ul>")
                with c2:
                    st.markdown('<div class="pe-h">Recommendations</div>', unsafe_allow_html=True)
                    render_html('<ul class="pe-list rec">' + "".join(
                        f'<li><span class="b">{i}</span><span>{esc(r)}</span></li>'
                        for i, r in enumerate(a.recommendations, 1)) + "</ul>")

            with tabs[5]:
                if not urep.available:
                    render_html(f'<div class="pe-empty"><div class="ic">{ICON_PHONE}</div><div>'
                                f'<div class="t">User assessment not available</div>'
                                f'<ol><li>{esc(urep.note)}</li><li>Grandstream UCM exports give the fullest per-person '
                                f'detail; Bicom gives a lighter version.</li></ol></div></div>')
                else:
                    tm = urep.team
                    if urep.note:
                        st.caption(urep.note)
                    utiles = [
                        ("People active", f"{tm['people']:,}", "made or answered calls", ""),
                        ("Inbound answered", f"{tm['answered']:,}", f"top: {tm['top']}", "good"),
                        ("Outbound calls", f"{tm['outbound']:,}",
                         f"{tm['connect_pct']:.0f}% connected" if tm["connect_pct"] == tm["connect_pct"] else "", "cyan"),
                        ("Avg time to answer", ce.fmt_dur(tm["avg_ring_s"]), "across the team", ""),
                        ("Talk time", ce.fmt_dur(tm["talk_s"]), "inbound + outbound", ""),
                        ("Missed calls returned", f"{tm['returned']:,}", f"{k['unreturned_callers']} callers still waiting",
                         "good" if tm["returned"] else "warn"),
                    ]
                    render_html('<div class="pe-kpis">' + "".join(
                        f'<div class="pe-kpi {t_}"><div class="l">{esc(l_)}</div><div class="v">{esc(v_)}</div>'
                        f'<div class="s">{esc(sub_)}</div></div>' for l_, v_, sub_, t_ in utiles) + "</div>")
                    st.markdown('<div class="pe-h">Team observations</div>', unsafe_allow_html=True)
                    render_html('<ul class="pe-list">' + "".join(
                        f'<li><span class="b">•</span><span>{esc(o)}</span></li>' for o in urep.team_observations) + "</ul>")

                    st.markdown('<div class="pe-h">Every person at a glance</div>', unsafe_allow_html=True)
                    uv = us.user_table_view(urep)
                    cfg = {}
                    if "Pick-up %" in uv:
                        cfg["Pick-up %"] = st.column_config.ProgressColumn("Pick-up %", min_value=0, max_value=100,
                                                                           format="%.0f%%")
                    cfg["Share of team %"] = st.column_config.ProgressColumn("Share of team %", min_value=0,
                                                                             max_value=max(1.0, float(uv["Share of team %"].max())),
                                                                             format="%.1f%%")
                    show_df(uv, column_config=cfg, height=min(560, 38 + 35 * len(uv)))
                    if len(urep.group_table):
                        st.markdown('<div class="pe-h">Ring groups</div>', unsafe_allow_html=True)
                        g = urep.group_table.sort_values("calls", ascending=False)
                        show_df(pd.DataFrame({
                            "Ring group": g["group"], "Members": g["members"], "Calls": g["calls"],
                            "Answered": g["answered"], "Answer %": g["answer_pct"].round(0), "Lost": g["lost"],
                            "People answering": g["answering_people"].astype(str) + " of " + g["members"].astype(str),
                            "Top answerer": g["top"] + " (" + g["top_pct"].round(0).astype(int).astype(str) + "%)"}))

                    st.markdown('<div class="pe-h">Individual assessment</div>', unsafe_allow_html=True)
                    t_ = urep.table
                    people = t_[(t_["handled"] > 0) | (t_["rang"] >= 40)]
                    pick_e = st.selectbox("Person", list(people.index),
                                          format_func=lambda e: f"{t_.loc[e, 'name']}" + (
                                              f"  (ext {e})" if urep.level == "full" else ""))
                    r = t_.loc[pick_e]
                    chips_ = chip(f"Ext {pick_e}", "accent") if urep.level == "full" else ""
                    for g_ in [g_ for g_ in str(r["groups"]).split(", ") if g_]:
                        chips_ += chip(f"Ring group {g_}")
                    if r["share_pct"] == r["share_pct"] and r["share_pct"] > 0:
                        chips_ += chip(f"{r['share_pct']:.0f}% of team's answered calls", "good")
                    render_html(chips_)
                    pt = [
                        ("Rang them", f"{int(r['rang']):,}", f"{int(r['lost'])} lost while ringing", ""),
                        ("Answered", f"{int(r['answered']):,}",
                         f"pick-up {r['pickup_pct']:.0f}%" if r["pickup_pct"] == r["pickup_pct"] else "", "good"),
                        ("Avg time to answer", ce.fmt_dur(r["avg_ring_s"]), "time to pick up", ""),
                        ("Outbound", f"{int(r['outbound']):,}",
                         f"{r['connect_pct']:.0f}% connected" if r["connect_pct"] == r["connect_pct"] else "", "cyan"),
                        ("Talk time", ce.fmt_dur(r["talk_s"]), f"{int(r['internal_made'])} internal calls", ""),
                        ("Missed calls returned", f"{int(r['returned']):,}", "rang back and spoke", ""),
                    ]
                    if urep.level != "full":
                        pt[0] = ("Share of team", f"{r['share_pct']:.0f}%", "of answered calls", "")
                    render_html('<div class="pe-kpis">' + "".join(
                        f'<div class="pe-kpi {t2}"><div class="l">{esc(l2)}</div><div class="v">{esc(v2)}</div>'
                        f'<div class="s">{esc(s2)}</div></div>' for l2, v2, s2, t2 in pt) + "</div>")
                    uc1, uc2 = st.columns([1.1, 1])
                    with uc1:
                        render_html('<ul class="pe-list">' + "".join(
                            f'<li><span class="b">•</span><span>{esc(o)}</span></li>'
                            for o in urep.observations.get(pick_e, [])) + "</ul>")
                    with uc2:
                        hp = urep.hourly.get(pick_e)
                        if hp is not None and hp.values.sum():
                            hp = hp.loc[7:19]
                            fu = go.Figure()
                            fu.add_bar(x=[f"{h:02d}:00" for h in hp.index], y=hp["inbound"], name="Inbound answered",
                                       marker_color="#7C83FF")
                            fu.add_bar(x=[f"{h:02d}:00" for h in hp.index], y=hp["outbound"], name="Outbound",
                                       marker_color="#38D6F5")
                            plot_layout(fu, 280).update_layout(title=dict(text="Calls by hour", font=dict(size=14)))
                            show_chart(fu)
                    st.download_button("Download user table (CSV)", uv.to_csv(index=False).encode(),
                                       "user_assessment.csv", "text/csv")

        # ----------------------------------------------------------------------- #
        # 04 Report
        # ----------------------------------------------------------------------- #
        with st.container(key="card-report"):
            section_header(f"{first_num + 1:02d}", "Download the report", "2-page A4 PDF: executive summary, charts, call-back list, "
                                                         "observations and recommendations")
            logo = logo_file.getvalue() if logo_file else None
            data_id = (a.provider, len(a.inbound), len(a.outbound), str(a.inbound["call_id"].head(20).tolist()))
            pdf_key = (data_id, p_start, p_end, settings_tuple, customer, brand, accent, len(logo or b""))
            with st.spinner("Building PDF..."):
                style = "print" if pdf_style.startswith("Print") else "portal"
                pdf = make_pdf(a, pdf_key + (style,), customer, brand, accent, logo, style)
            fname = f"Call_Assessment_{(customer or 'Customer').replace(' ', '_')}_{p_start:%Y%m%d}-{p_end:%Y%m%d}.pdf"
            c1, c2 = st.columns([1, 2])
            c1.download_button("Download PDF report", pdf, fname, "application/pdf", type="primary", **FULL_WIDTH)
            if not portal:
                c2.caption("Tip: add the customer's name and your logo in the sidebar before downloading.")
            if urep.available:
                n_people = int(((urep.table["handled"] > 0) | (urep.table["rang"] >= 40)).sum())
                st.markdown('<div class="pe-h" style="margin-top:14px">User assessment</div>', unsafe_allow_html=True)
                u_key = ("upk", pdf_key, style)
                c3, c4 = st.columns([1, 2])
                if st.session_state.get("user_pdf_key") == u_key:
                    with st.spinner("Building user assessment PDF..."):
                        updf = make_user_pdf(a, urep, u_key, customer, brand, accent, logo, style)
                    c3.download_button("Download user assessment PDF", updf,
                                       fname.replace("Call_Assessment", "User_Assessment"), "application/pdf",
                                       type="primary", **FULL_WIDTH)
                elif c3.button("Build user assessment PDF", **FULL_WIDTH):
                    st.session_state["user_pdf_key"] = u_key
                    st.rerun()
                c4.caption(f"Team overview, ring groups and a one-card assessment for each of the {n_people} people - "
                           f"{2 + -(-n_people // 3)} pages.")
            stage = 4
    return stage
