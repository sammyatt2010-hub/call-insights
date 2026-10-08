"""
Call Insights - upload a raw PBX CDR export, get a true picture of missed calls and a 2-page PDF.
"""
from __future__ import annotations

import html as html_lib
import inspect
from datetime import time

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import cdr_engine as ce
from report_pdf import build_pdf

st.set_page_config(page_title="Call Insights", page_icon="📞", layout="wide")

# --------------------------------------------------------------------------- #
# Design system
# --------------------------------------------------------------------------- #
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
.st-key-card-upload,.st-key-card-period,.st-key-card-results,.st-key-card-report{background:linear-gradient(180deg,rgba(22,31,51,.85) 0%,rgba(17,24,39,.85) 100%);border:1px solid var(--border)!important;border-radius:var(--radius);padding:22px 22px 18px;box-shadow:0 1px 0 rgba(255,255,255,.03) inset,0 20px 40px -24px rgba(0,0,0,.6);margin-bottom:18px}
[data-baseweb="input"],[data-baseweb="select"]>div,[data-baseweb="textarea"]{background:var(--surface)!important;border:1px solid var(--border-strong)!important;border-radius:10px!important}
[data-baseweb="input"]:focus-within,[data-baseweb="select"]>div:focus-within,[data-baseweb="textarea"]:focus-within{border-color:var(--accent)!important;box-shadow:0 0 0 3px var(--accent-soft)!important}
[data-baseweb="input"]>div,[data-baseweb="base-input"]{background:transparent!important}
.stButton button,.stDownloadButton button,.stFormSubmitButton button{border-radius:10px!important;font-weight:600!important;border:1px solid var(--border-strong)!important;background:var(--surface-2)!important;color:var(--text)!important;transition:all .15s ease}
.stButton button:hover,.stDownloadButton button:hover{border-color:var(--accent)!important;transform:translateY(-1px)}
.stButton button[kind="primary"],.stDownloadButton button[kind="primary"],[data-testid="stBaseButton-primary"]{background:var(--grad)!important;border:none!important;color:#0A0E1A!important;box-shadow:0 8px 24px -10px rgba(124,131,255,.8)}
.stButton button[kind="primary"] p,.stDownloadButton button[kind="primary"] p,[data-testid="stBaseButton-primary"] p{color:#0A0E1A!important;font-weight:700!important}
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
def load_files(blobs: tuple[bytes, ...]) -> pd.DataFrame:
    return ce.load_many(list(blobs))


@st.cache_data(show_spinner=False)
def run_analysis(df: pd.DataFrame, start, end, settings_tuple):
    s = ce.Settings(*settings_tuple)
    return ce.analyse(df, start, end, s)


@st.cache_data(show_spinner=False)
def make_pdf(_a, key, customer, brand, accent, logo):
    return build_pdf(_a, customer, brand, accent, logo)


# --------------------------------------------------------------------------- #
# Sidebar - settings
# --------------------------------------------------------------------------- #
with st.sidebar:
    render_html(f'<div class="pe-brand"><div class="pe-logo">{ICON_PHONE}</div><div><div class="n">Call Insights</div>'
                f'<div class="tg">True missed-call reporting</div></div></div>')
    st.markdown("**Opening hours**")
    c1, c2 = st.columns(2)
    slots = [time(h, m) for h in range(24) for m in (0, 15, 30, 45)]
    open_t = c1.selectbox("Open", slots, index=slots.index(time(8, 30)), format_func=lambda t: t.strftime("%H:%M"))
    close_t = c2.selectbox("Close", slots, index=slots.index(time(17, 30)), format_func=lambda t: t.strftime("%H:%M"))
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    open_days = st.multiselect("Open days", day_names, default=day_names[:5])
    st.markdown("**Targets & rules**")
    target = st.slider("Answer-rate target %", 70, 100, 90)
    cb_target = st.select_slider("Call-back target", options=[15, 30, 60, 120, 240, 480], value=60,
                                 format_func=lambda m: f"{m} min" if m < 60 else f"{m // 60} hr")
    short_ab = st.number_input("Ignore hang-ups under (seconds)", 0, 30, 5,
                               help="Callers who hang up this quickly never gave you a fair chance to answer.")
    with st.expander("Exclude callers"):
        excl_int = st.checkbox("Exclude international numbers", value=False)
        excl_txt = st.text_area("Numbers to exclude (one per line)", placeholder="07700 900123",
                                help="e.g. known spam, test calls, or the customer's own mobiles.")
    st.markdown("**Report branding**")
    brand = st.text_input("Prepared by", "SY Communications")
    accent = st.color_picker("Accent colour", "#4F46E5")
    logo_file = st.file_uploader("Logo for PDF (optional)", type=["png", "jpg", "jpeg"])

settings_tuple = (
    open_t, close_t, tuple(day_names.index(d) for d in open_days), int(short_ab), float(target), int(cb_target), 4,
    tuple(x.strip() for x in excl_txt.splitlines() if x.strip()), bool(excl_int),
)

# --------------------------------------------------------------------------- #
# Hero (filled at the end so the stepper reflects this run)
# --------------------------------------------------------------------------- #
hero = st.empty()
stage = 1

# --------------------------------------------------------------------------- #
# 01 Upload
# --------------------------------------------------------------------------- #
df = None
with st.container(key="card-upload"):
    section_header("01", "Upload call records", "Raw CDR export from the PBX - CSV or tab-separated, any date range")
    files = st.file_uploader("CDR file(s)", type=["csv", "txt", "tsv"], accept_multiple_files=True,
                             label_visibility="collapsed")
    if files:
        try:
            with st.spinner("Reading call records..."):
                df = load_files(tuple(f.getvalue() for f in files))
            dmin, dmax = ce.detect_range(df)
            render_html(chip(f"{len(df):,} call legs", "accent") + chip(f"{dmin:%d %b %Y} → {dmax:%d %b %Y}")
                        + chip(f"{(dmax - dmin).days + 1} days of data") + chip(f"{len(files)} file(s)", "good"))
            stage = 2
        except Exception as e:  # noqa: BLE001
            st.error(f"Couldn't read that file - is it the raw CDR export? ({e})")
    else:
        render_html(f'<div class="pe-empty"><div class="ic">{ICON_UPLOAD}</div><div>'
                    f'<div class="t">Drop in a CDR export to get started</div><ol>'
                    f'<li>Export the call records from the PBX for any period</li>'
                    f'<li>Upload it here - the date range is detected automatically</li>'
                    f'<li>Pick the reporting period and download the 2-page PDF</li></ol></div></div>')

# --------------------------------------------------------------------------- #
# 02 Customer & period
# --------------------------------------------------------------------------- #
a = None
if df is not None:
    with st.container(key="card-period"):
        section_header("02", "Customer & reporting period", "Periods are relative to the latest call in the file")
        c1, c2 = st.columns([1, 1.4])
        customer = c1.text_input("Customer name", placeholder="e.g. Acme Recruitment Ltd")
        presets = ["Whole file", "This month", "Last month", "Last 30 days", "Last 7 days", "Custom"]
        preset = c2.radio("Period", presets, horizontal=True)
        if preset == "Custom":
            rng = st.date_input("Custom range", (dmin, dmax), min_value=dmin, max_value=dmax, format="DD/MM/YYYY")
            p_start, p_end = (rng if isinstance(rng, (list, tuple)) and len(rng) == 2 else (dmin, dmax))
        else:
            p_start, p_end = ce.preset_range(preset, dmin, dmax)
        render_html(chip(f"Reporting {p_start:%d %b %Y} → {p_end:%d %b %Y}", "accent")
                    + chip("Call-backs are searched across the whole file"))
        if p_start > p_end:
            st.warning("That period has no data in this file - pick another.")
        else:
            with st.spinner("Stitching call legs and tracking call-backs..."):
                a = run_analysis(df, p_start, p_end, settings_tuple)
            stage = 3

# --------------------------------------------------------------------------- #
# 03 Results
# --------------------------------------------------------------------------- #
def plot_layout(fig, h=340):
    fig.update_layout(height=h, margin=dict(l=10, r=10, t=78, b=10), paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", font=dict(family="Inter", color="#C7CEDD", size=12),
                      legend=dict(orientation="h", y=1.0, yanchor="bottom", x=0, traceorder="normal"), barmode="stack", hovermode="x unified")
    fig.update_xaxes(gridcolor="rgba(148,163,184,.08)", zeroline=False)
    fig.update_yaxes(gridcolor="rgba(148,163,184,.10)", zeroline=False)
    return fig


if a is not None and a.kpis["inbound_total"] == 0:
    with st.container(key="card-results"):
        st.info("No inbound calls in this period.")
elif a is not None:
    k, s = a.kpis, a.settings
    with st.container(key="card-results"):
        section_header("03", "Call handling at a glance", f"{a.period_start:%d %b %Y} → {a.period_end:%d %b %Y}")
        ar = k["answer_rate_ih"]
        tone = "good" if ar >= s.answer_target_pct else ("warn" if ar >= s.answer_target_pct - 10 else "bad")
        tiles = [
            ("Inbound calls", f"{k['inbound_total']:,}", f"{k['avg_inbound_per_open_day']:.0f} per working day", ""),
            ("Answered in hours", f"{ar:.1f}%", f"target {s.answer_target_pct:.0f}%", tone),
            ("Truly missed", f"{k['true_missed']:,}", f"PBX log said {k['pbx_reported_missed']:,}", "bad"),
            ("Saved by overflow", f"{k['answered_overflow']:,}",
             f"{100 * k['answered_overflow'] / max(1, k['answered']):.0f}% of answered", "cyan"),
            ("Un-returned callers", f"{k['unreturned_callers']:,}", f"{k['unreturned_calls']:,} missed calls",
             "bad" if k["unreturned_callers"] else "good"),
            ("Avg time to answer", ce.fmt_dur(k["avg_ring_s"]), f"median call-back {ce.fmt_mins(k['median_callback_min'])}", ""),
        ]
        render_html('<div class="pe-kpis">' + "".join(
            f'<div class="pe-kpi {t}"><div class="l">{esc(l)}</div><div class="v">{esc(v)}</div>'
            f'<div class="s">{esc(sub)}</div></div>' for l, v, sub, t in tiles) + "</div>")

        tabs = st.tabs(["Overview", "Missed calls", "Un-returned callers", "Lines & team", "Insights"])

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
                                name=col, marker_color=colr)
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
            render_html(chip(f"{len(m):,} truly missed", "bad") + chip(f"{k['voicemail']:,} to voicemail", "warn")
                        + chip(f"{k['missed_resolved']:,} resolved", "good")
                        + chip(f"{k['short_abandons']:,} quick hang-ups excluded"))
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
                "missed": "Missed", "answer_rate": "Answer %", "unreturned_callers": "Un-returned callers"})
            lv["Answer %"] = lv["Answer %"].round(1)
            show_df(lv, column_config={"Answer %": st.column_config.ProgressColumn(
                "Answer %", min_value=0, max_value=100, format="%.0f%%")})
            st.markdown('<div class="pe-h">Team - outbound & call-backs</div>', unsafe_allow_html=True)
            tv = a.team.reset_index().rename(columns={
                "agent": "Extension", "outbound": "Outbound calls", "connected": "Connected",
                "talk_s": "Talk time", "missed_calls_returned": "Missed calls returned"})
            tv["Talk time"] = tv["Talk time"].map(ce.fmt_dur)
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

    # ----------------------------------------------------------------------- #
    # 04 Report
    # ----------------------------------------------------------------------- #
    with st.container(key="card-report"):
        section_header("04", "Download the report", "2-page A4 PDF: executive summary, charts, call-back list, "
                                                     "observations and recommendations")
        logo = logo_file.getvalue() if logo_file else None
        pdf_key = (p_start, p_end, settings_tuple, customer, brand, accent, len(logo or b""))
        with st.spinner("Building PDF..."):
            pdf = make_pdf(a, pdf_key, customer, brand, accent, logo)
        fname = f"Call_Assessment_{(customer or 'Customer').replace(' ', '_')}_{p_start:%Y%m%d}-{p_end:%Y%m%d}.pdf"
        c1, c2 = st.columns([1, 2])
        c1.download_button("Download PDF report", pdf, fname, "application/pdf", type="primary", **FULL_WIDTH)
        c2.caption("Tip: add the customer's name and your logo in the sidebar before downloading.")
        stage = 4

# --------------------------------------------------------------------------- #
# Hero
# --------------------------------------------------------------------------- #
steps = ["Upload", "Period", "Review", "Report"]
step_html = '<span class="pe-step-sep"></span>'.join(
    f'<div class="pe-step {"done" if i + 1 < stage else ("active" if i + 1 == stage else "")}">'
    f'<span class="num">{"&#10003;" if i + 1 < stage else i + 1}</span>{s_}</div>'
    for i, s_ in enumerate(steps))
render_html(f'<div class="pe-hero"><div><div class="pe-eyebrow"><span class="dot"></span>CDR analysis</div>'
            f'<div class="pe-title">Call handling <span>assessment</span></div>'
            f'<div class="pe-sub">See what really happened to every call: missed calls the overflow group rescued, '
            f'calls that were genuinely lost, and the customers nobody called back.</div></div>'
            f'<div class="pe-stepper">{step_html}</div></div>', hero)
