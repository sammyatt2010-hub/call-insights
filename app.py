"""
Call Insights - upload a raw PBX CDR export, get a true picture of missed calls and a 2-page PDF.
"""
from __future__ import annotations

from datetime import time

import streamlit as st

import cdr_engine as ce
from ui import (ICON_PHONE, ICON_UPLOAD, chip, inject_css, load_files, render_html, render_results, run_analysis,
                section_header)

st.set_page_config(page_title="Call Insights", page_icon="📞", layout="wide")
inject_css()

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
                               help="Callers who hang up this quickly never gave you a fair chance to answer. "
                                    "(Bicom only - TruSIP logs don't record ring time.)")
    with st.expander("Exclude callers"):
        excl_int = st.checkbox("Exclude international numbers", value=False)
        excl_txt = st.text_area("Numbers to exclude (one per line)", placeholder="07700 900123",
                                help="e.g. known spam, test calls, or the customer's own mobiles.")
    st.markdown("**Report branding**")
    brand = st.text_input("Prepared by", "SY Communications")
    pdf_style = st.radio("PDF style", ["Portal (dark)", "Print-friendly (light)"], horizontal=False,
                         help="Portal matches this web app. Print-friendly is the same layout on white paper.")
    accent = st.color_picker("Accent colour", "#7C83FF")
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
    section_header("01", "Upload call records", "Pick the phone system, then drop in its call-log export - any date range")
    pc1, pc2 = st.columns([1, 2.2])
    provider = pc1.selectbox("Phone system", list(ce.PROVIDERS), format_func=ce.PROVIDERS.get,
                             help="Each phone system exports call records differently.")
    pc2.caption({
        "bicom": "Bicom CDR export (tab-separated). Shows every ring group, overflow and voicemail step, so overflow "
                 "rescues and time-to-answer are reported.",
        "vela": "Vela Networks call log (CSV). One row per call with the IVR, ring groups (incl. overflow), routing "
                "and talk time - menu hang-ups, overflow rescues and time-to-answer are reported.",
        "grandstream": "Grandstream UCM CDR export (CSV, or the .tar / .zip download as-is). Shows every ring group, "
                       "extension and voicemail step, so who answered, overflow and time-to-answer are reported.",
        "trusip": "TruSIP call log (CSV). One row per call at line level: answered / missed / engaged and call-backs "
                  "are reported; ring groups, overflow and time-to-answer are not in this export.",
    }[provider])
    files = st.file_uploader("CDR file(s)", type=None, accept_multiple_files=True,
                             label_visibility="collapsed")
    if files:
        try:
            blobs = tuple(f.getvalue() for f in files)
            guess = ce.detect_provider(blobs[0])
            if guess and guess != provider:
                st.warning(f"This file looks like a **{ce.PROVIDERS[guess]}** export, but **{ce.PROVIDERS[provider]}** "
                           f"is selected. Switch the phone system above if the results look wrong.")
            with st.spinner("Reading call records..."):
                df = load_files(blobs, provider)
            dmin, dmax = ce.detect_range(df)
            dropped = df.attrs.get("dropped", {})
            extra = ""
            if dropped.get("internal"):
                extra += chip(f"{dropped['internal']} internal calls ignored")
            if dropped.get("transfers"):
                extra += chip(f"{dropped['transfers']} colleague transfers merged")
            if dropped.get("forwarded_legs"):
                extra += chip(f"{dropped['forwarded_legs']} forwarding legs merged")
            render_html(chip(ce.PROVIDERS[provider], "accent")
                        + chip(f"{len(df):,} {'call records' if provider == 'trusip' else 'call legs'}", "accent") + chip(f"{dmin:%d %b %Y} → {dmax:%d %b %Y}")
                        + chip(f"{(dmax - dmin).days + 1} days of data") + chip(f"{len(files)} file(s)", "good") + extra)
            stage = 2
        except Exception as e:  # noqa: BLE001
            st.error(f"Couldn't read that file as a {ce.PROVIDERS[provider]} export - check the phone system "
                     f"selected above. ({e})")
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
if a is not None:
    stage = render_results(df, a, p_start, p_end, settings_tuple, customer, brand, accent, logo_file,
                           pdf_style)

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
            f'<div class="pe-sub">See what really happened to every call: which calls were genuinely lost, how '
            f'quickly they were returned, and the customers nobody called back.</div></div>'
            f'<div class="pe-stepper">{step_html}</div></div>', hero)
