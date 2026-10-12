"""
report_pdf.py - the 2-page A4 "Call Handling Assessment" PDF.
Page 1: headline KPIs, executive summary, when calls arrive, what happened to them.
Page 2: missed-call follow-up, lines/branches, un-returned callers, observations & recommendations.
"""
from __future__ import annotations

import io
from datetime import datetime
from xml.sax.saxutils import escape

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, Table, TableStyle

from cdr_engine import Analysis, fmt_dur, fmt_mins, pretty_number

W, H = A4
M = 14 * mm  # page margin

NAVY = colors.HexColor("#0F172A")
INK = colors.HexColor("#1E293B")
MUTED = colors.HexColor("#64748B")
LINE = colors.HexColor("#E2E8F0")
SOFT = colors.HexColor("#F1F5F9")
GOOD = colors.HexColor("#059669")
WARN = colors.HexColor("#D97706")
BAD = colors.HexColor("#DC2626")

C_ANS, C_OVF, C_MISS, C_VM = "#4F46E5", "#22B8CF", "#EF4444", "#F59E0B"


def _hex(c):
    return colors.HexColor(c) if isinstance(c, str) else c


def _style(size=8.5, color=INK, bold=False, leading=None, align=TA_LEFT):
    return ParagraphStyle("x", fontName="Helvetica-Bold" if bold else "Helvetica", fontSize=size,
                          leading=leading or size * 1.35, textColor=color, alignment=align)


def _para(c, text, x, y_top, width, style):
    p = Paragraph(text, style)
    _, h = p.wrap(width, 1000)
    p.drawOn(c, x, y_top - h)
    return h


def _fig_to_img(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=220, bbox_inches="tight", transparent=False, facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return ImageReader(buf)


def _setup_ax(ax):
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_color("#CBD5E1")
    ax.tick_params(colors="#475569", labelsize=7)
    ax.grid(axis="y", color="#EEF2F7", linewidth=0.8)
    ax.set_axisbelow(True)


# --------------------------------------------------------------------------- #
# Charts
# --------------------------------------------------------------------------- #
def chart_hourly(a: Analysis, w_mm, h_mm):
    inb = a.inbound[~a.inbound["outcome"].isin(["Short abandon", "Failed"])]
    hrs = inb.assign(h=inb["ts"].dt.hour)
    tab = hrs.pivot_table(index="h", columns="outcome", values="call_id", aggfunc="count", fill_value=0)
    active = tab.sum(axis=1)
    active = active[active > 0]
    sig = active[active >= max(1, active.sum() * 0.003)]
    lo, hi = (int(sig.index.min()), int(sig.index.max())) if len(sig) else (8, 18)
    lo, hi = max(0, min(lo, 8)), min(23, max(hi, 17))
    tab = tab.reindex(range(lo, hi + 1), fill_value=0)
    days = max(1, a.kpis["days"])
    get = lambda col: tab[col].values / days if col in tab else np.zeros(len(tab))
    if a.caps.get("routing", True):
        series = [("Answered", get("Answered"), C_ANS), ("Answered via overflow", get("Answered via overflow"), C_OVF),
                  ("Missed", get("Missed") + get("Busy"), C_MISS), ("Voicemail", get("Voicemail"), C_VM)]
    else:
        series = [("Answered", get("Answered"), C_ANS), ("Missed (no answer)", get("Missed"), C_MISS),
                  ("Engaged (busy)", get("Busy"), C_VM)]

    fig, ax = plt.subplots(figsize=(w_mm / 25.4, h_mm / 25.4))
    x = np.arange(len(tab))
    bottom = np.zeros(len(tab))
    for label, vals, col in series:
        ax.bar(x, vals, bottom=bottom, color=col, width=0.72, label=label)
        bottom = bottom + vals
    ax.set_xticks(x)
    ax.set_xticklabels([f"{h:02d}" for h in tab.index])
    ax.set_ylabel("Avg calls per day", fontsize=7, color="#475569")
    _setup_ax(ax)
    ax.legend(fontsize=6.5, frameon=False, ncol=4, loc="upper center", bbox_to_anchor=(0.5, 1.13))
    fig.tight_layout()
    return _fig_to_img(fig)


def chart_reality(a: Analysis, w_mm, h_mm):
    k = a.kpis
    fig, ax = plt.subplots(figsize=(w_mm / 25.4, h_mm / 25.4))
    if a.caps.get("routing", True):
        labels = ["Phone system log\n'not answered'", "Truly missed\n(after overflow)", "Never returned\n(callers)"]
    else:
        labels = ["Missed calls\n(incl. engaged)", "Missed calls\nnot resolved", "Never returned\n(callers)"]
        k = dict(k, pbx_reported_missed=k["true_missed"], true_missed=k["unreturned_calls"])
    vals = [k["pbx_reported_missed"], k["true_missed"], k["unreturned_callers"]]
    cols = ["#CBD5E1", C_MISS, "#991B1B"]
    bars = ax.barh(labels[::-1], vals[::-1], color=cols[::-1], height=0.58)
    for b, v in zip(bars, vals[::-1]):
        ax.text(b.get_width() + max(vals) * 0.02, b.get_y() + b.get_height() / 2, f"{v:,}",
                va="center", fontsize=8, color="#0F172A", fontweight="bold")
    ax.set_xlim(0, max(vals) * 1.2 if max(vals) else 1)
    _setup_ax(ax)
    ax.grid(False)
    ax.spines["bottom"].set_visible(False)
    ax.set_xticks([])
    ax.tick_params(axis="y", labelsize=7)
    fig.tight_layout()
    return _fig_to_img(fig)


def chart_heat(a: Analysis, w_mm, h_mm):
    heat = a.heat.copy()
    tot = heat.values.sum()
    cols = [h for h in heat.columns if heat[h].sum() >= max(1, tot * 0.003)] or list(range(8, 18))
    lo, hi = min(min(cols), 8), max(max(cols), 17)
    heat = heat.loc[:, lo:hi]
    rows = [d for d in range(7) if heat.loc[d].sum() > 0] or list(range(5))
    heat = heat.loc[rows]
    weeks = max(1, a.kpis["days"] / 7)
    vals = heat.values / weeks
    fig, ax = plt.subplots(figsize=(w_mm / 25.4, h_mm / 25.4))
    ax.imshow(vals, aspect="auto", cmap="Blues", vmin=0)
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([names[d] for d in rows], fontsize=7)
    ax.set_xticks(range(vals.shape[1]))
    ax.set_xticklabels([f"{h:02d}" for h in heat.columns], fontsize=6.5)
    vmax = vals.max() if vals.size else 1
    for i in range(vals.shape[0]):
        for j in range(vals.shape[1]):
            v = vals[i, j]
            if v >= 0.5:
                ax.text(j, i, f"{v:.0f}", ha="center", va="center", fontsize=5.8,
                        color="white" if v > vmax * 0.55 else "#1E293B")
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.tick_params(length=0, colors="#475569")
    fig.tight_layout()
    return _fig_to_img(fig)


def chart_followup(a: Analysis, w_mm, h_mm):
    m = a.missed
    order = ["Called back - spoke", "Customer rang again - answered", "Call-back attempted - no answer",
             "Not returned", "Withheld number", "Too recent to judge"]
    cmap = {"Called back - spoke": "#059669", "Customer rang again - answered": "#34D399",
            "Call-back attempted - no answer": "#F59E0B", "Not returned": "#DC2626",
            "Withheld number": "#94A3B8", "Too recent to judge": "#CBD5E1"}
    counts = m["follow_up"].value_counts().reindex(order).fillna(0)
    counts = counts[counts > 0]
    fig, ax = plt.subplots(figsize=(w_mm / 25.4, h_mm / 25.4))
    if counts.sum() == 0:
        ax.text(0.5, 0.5, "No missed calls", ha="center", va="center")
        ax.axis("off")
        return _fig_to_img(fig)
    wedges, _ = ax.pie(counts.values, colors=[cmap[i] for i in counts.index], startangle=90, counterclock=False,
                       wedgeprops=dict(width=0.38, edgecolor="white", linewidth=1.5))
    ax.text(0, 0.08, f"{int(counts.sum()):,}", ha="center", va="center", fontsize=13, fontweight="bold",
            color="#0F172A")
    ax.text(0, -0.2, "missed calls", ha="center", va="center", fontsize=6.5, color="#64748B")
    ax.legend(wedges, [f"{i}  ({int(v):,})" for i, v in counts.items()], fontsize=6.3, frameon=False,
              loc="center left", bbox_to_anchor=(1.0, 0.5))
    ax.set_aspect("equal")
    fig.tight_layout()
    return _fig_to_img(fig)


# --------------------------------------------------------------------------- #
# Building blocks
# --------------------------------------------------------------------------- #
def _header(c, title, customer, period, brand, accent, logo):
    hh = 30 * mm
    c.setFillColor(NAVY)
    c.rect(0, H - hh, W, hh, stroke=0, fill=1)
    c.setFillColor(_hex(accent))
    c.rect(0, H - hh, W, 1.6 * mm, stroke=0, fill=1)
    x = M
    if logo is not None:
        try:
            img = ImageReader(io.BytesIO(logo))
            iw, ih = img.getSize()
            lh = 13 * mm
            lw = min(40 * mm, lh * iw / ih)
            c.drawImage(img, W - M - lw, H - hh + (hh - lh) / 2 + 1 * mm, lw, lh, mask="auto",
                        preserveAspectRatio=True)
        except Exception:
            pass
    c.setFillColor(_hex(accent))
    c.setFont("Helvetica-Bold", 7.5)
    c.drawString(x, H - 10 * mm, title.upper())
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 17)
    c.drawString(x, H - 17.5 * mm, customer or "Call Handling Assessment")
    c.setFillColor(colors.HexColor("#CBD5E1"))
    c.setFont("Helvetica", 8.5)
    c.drawString(x, H - 23.5 * mm, f"{period}   |   Prepared by {brand}")
    return H - hh


def _footer(c, page, brand, generated):
    c.setStrokeColor(LINE)
    c.line(M, 11 * mm, W - M, 11 * mm)
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 6.5)
    c.drawString(M, 7.5 * mm, f"{brand}  -  Call Handling Assessment  -  generated {generated}")
    c.drawRightString(W - M, 7.5 * mm, f"Page {page} of 2")


def _section(c, text, x, y, accent):
    c.setFillColor(_hex(accent))
    c.rect(x, y - 3.6 * mm, 1.2 * mm, 4 * mm, stroke=0, fill=1)
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(x + 3 * mm, y - 3 * mm, text)
    return y - 6.5 * mm


def _kpi(c, x, y, w, h, label, value, sub, tone):
    c.setFillColor(SOFT)
    c.roundRect(x, y - h, w, h, 2.2 * mm, stroke=0, fill=1)
    c.setFillColor(tone)
    c.roundRect(x, y - 1.3 * mm, w, 1.3 * mm, 0.6 * mm, stroke=0, fill=1)
    c.setFillColor(MUTED)
    c.setFont("Helvetica-Bold", 6.2)
    c.drawString(x + 3 * mm, y - 6 * mm, label.upper())
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(x + 3 * mm, y - 13.2 * mm, value)
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 6.4)
    c.drawString(x + 3 * mm, y - 17.4 * mm, sub)


def _table(c, data, x, y_top, col_w, header_bg, font=6.8, zebra=True, align_right_from=1):
    t = Table(data, colWidths=col_w, rowHeights=[5.4 * mm] + [4.7 * mm] * (len(data) - 1))
    st = [
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", font),
        ("FONT", (0, 1), (-1, -1), "Helvetica", font),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 0), (-1, 0), _hex(header_bg)),
        ("TEXTCOLOR", (0, 1), (-1, -1), INK),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (align_right_from, 0), (-1, -1), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, LINE),
    ]
    if zebra:
        for r in range(2, len(data), 2):
            st.append(("BACKGROUND", (0, r), (-1, r), colors.HexColor("#F8FAFC")))
    t.setStyle(TableStyle(st))
    _, h = t.wrap(sum(col_w), 1000)
    t.drawOn(c, x, y_top - h)
    return h


def _trunc(s, n):
    s = str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def build_pdf(a: Analysis, customer: str = "", brand: str = "SY Communications", accent: str = "#4F46E5",
              logo: bytes | None = None) -> bytes:
    k, s = a.kpis, a.settings
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"Call Handling Assessment - {customer}".strip(" -"))
    c.setAuthor(brand)
    period = f"{a.period_start:%d %b %Y} - {a.period_end:%d %b %Y}"
    generated = datetime.now().strftime("%d %b %Y")
    cw = W - 2 * M

    # ---------------- PAGE 1 ----------------
    y = _header(c, "Call Handling Assessment", customer, period, brand, accent, logo) - 7 * mm

    ar = k["answer_rate_ih"]
    routing, ring = a.caps.get("routing", True), a.caps.get("ring_time", True)
    ar_tone = GOOD if ar >= s.answer_target_pct else (WARN if ar >= s.answer_target_pct - 10 else BAD)
    tiles = [
        ("Inbound calls", f"{k['inbound_total']:,}", f"{k['avg_inbound_per_open_day']:.0f} per working day", _hex(accent)),
        ("Answered in hours", f"{ar:.1f}%", f"target {s.answer_target_pct:.0f}%", ar_tone),
        ("Truly missed", f"{k['true_missed']:,}", f"PBX log showed {k['pbx_reported_missed']:,}", BAD)
        if routing else ("Missed calls", f"{k['true_missed']:,}", f"{100 - k['answer_rate']:.1f}% of all inbound", BAD),
        ("Engaged / busy", f"{k['busy']:,}", "callers got a busy tone", WARN if k["busy"] else GOOD)
        if not routing else ("Saved by overflow", f"{k['answered_overflow']:,}",
         f"{100 * k['answered_overflow'] / max(1, k['answered']):.0f}% of answered calls", colors.HexColor(C_OVF)),
        ("Un-returned callers", f"{k['unreturned_callers']:,}", f"{k['unreturned_calls']:,} missed calls",
         BAD if k["unreturned_callers"] else GOOD),
        ("Avg time to answer", fmt_dur(k["avg_ring_s"]), f"avg talk {fmt_dur(k['avg_talk_s'])}", _hex(accent))
        if ring else ("Avg call length", fmt_dur(k["avg_talk_s"]),
                      "per answered call", _hex(accent)),
    ]
    gap = 2.6 * mm
    tw = (cw - gap * 5) / 6
    for i, (l, v, sub, tone) in enumerate(tiles):
        _kpi(c, M + i * (tw + gap), y, tw, 20 * mm, l, v, sub, tone)
    y -= 27 * mm

    y = _section(c, "Executive summary", M, y, accent)
    y -= _para(c, escape(a.summary), M, y, cw, _style(8.6, INK, leading=12.4)) + 6 * mm

    half = (cw - 6 * mm) / 2
    y = _section(c, "When calls arrive - average calls per day, by hour", M, y, accent)
    _section(c, "", M, y, accent) if False else None
    ch_h = 62
    c.drawImage(chart_hourly(a, cw / mm, ch_h), M, y - ch_h * mm, cw, ch_h * mm)
    y -= ch_h * mm + 6 * mm

    y = _section(c, "Missed calls - phone log vs reality" if routing else "Missed calls - how many were resolved",
                 M, y, accent)
    _section(c, "What happened to missed callers", M + half + 6 * mm, y + 6.5 * mm, accent)
    ch2 = 48
    c.drawImage(chart_reality(a, half / mm, ch2), M, y - ch2 * mm, half, ch2 * mm)
    c.drawImage(chart_followup(a, half / mm, ch2), M + half + 6 * mm, y - ch2 * mm, half, ch2 * mm)
    y -= ch2 * mm + 3 * mm

    if routing:
        note = (f"<b>How to read this:</b> the phone system writes a separate entry for every ring group, overflow "
                f"group, menu and voicemail a call passes through, so one caller can appear as several 'not answered' "
                f"rows. These have been stitched back into {k['inbound_total']:,} real calls. A call only counts as "
                f"missed if no person answered it at any stage. Opening hours used: "
                f"{s.open_time:%H:%M}-{s.close_time:%H:%M}. Callers who hung up within {s.short_abandon_s}s are excluded.")
    else:
        note = (f"<b>How to read this:</b> this report is built from the line-level call log, which records one entry "
                f"per call: answered, missed (rang with no answer) or engaged (busy tone). Calls between the business's "
                f"own sites and call-forwarding legs have been removed. A missed call counts as followed up if the "
                f"caller was later called back and connected, or rang again and was answered. Opening hours used: "
                f"{s.open_time:%H:%M}-{s.close_time:%H:%M}.")
    _para(c, note, M, y, cw, _style(6.8, MUTED, leading=9.2))
    _footer(c, 1, brand, generated)
    c.showPage()

    # ---------------- PAGE 2 ----------------
    y = _header(c, "Call Handling Assessment", customer, period, brand, accent, logo) - 7 * mm

    y = _section(c, "Busiest times - average inbound calls per week", M, y, accent)
    hh = 44
    c.drawImage(chart_heat(a, cw / mm, hh), M, y - hh * mm, cw, hh * mm)
    y -= hh * mm + 5 * mm

    # Lines table (left) + Un-returned callers (right)
    left_w = cw * 0.50
    right_w = cw - left_w - 5 * mm
    y0 = y
    _section(c, "Lines & branches", M, y0, accent)
    rows = [["Line / branch", "Calls", "Ans %", "Missed", "O'flow" if routing else "Busy", "Unret."]]
    for name, r in a.lines.head(12).iterrows():
        rows.append([_trunc(name, 34), f"{int(r.calls):,}", f"{r.answer_rate:.0f}%", f"{int(r.missed):,}",
                     f"{int(r.overflow if routing else r.busy):,}", f"{int(r.unreturned_callers):,}"])
    lw = [left_w * f for f in (0.44, 0.11, 0.11, 0.11, 0.11, 0.12)]
    h1 = _table(c, rows, M, y0 - 6.5 * mm, lw, NAVY)

    xr = M + left_w + 5 * mm
    _section(c, "Callers never spoken to (top 12)", xr, y0, accent)
    ur = a.unreturned_callers.head(12)
    rows2 = [["Caller", "Missed", "Last missed", "Status"]]
    for r in ur.itertuples():
        rows2.append([pretty_number(r.caller), str(int(r.missed_calls)), f"{r.last_missed:%d %b %H:%M}",
                      "Tried, no answer" if r.follow_up.startswith("Call-back") else "Not returned"])
    if len(rows2) == 1:
        rows2.append(["None - every missed caller was followed up", "", "", ""])
    rw = [right_w * f for f in (0.32, 0.14, 0.26, 0.28)]
    h2 = _table(c, rows2, xr, y0 - 6.5 * mm, rw, "#991B1B", align_right_from=1)
    y = y0 - 6.5 * mm - max(h1, h2) - 3 * mm
    extra = []
    if len(a.unreturned_callers) > 12:
        extra.append(f"+{len(a.unreturned_callers) - 12} more un-returned callers in the full export.")
    if k.get("median_callback_min") == k.get("median_callback_min"):
        extra.append(f"Median time to call back a missed call: {fmt_mins(k['median_callback_min'])}.")
    if extra:
        _para(c, " ".join(extra), xr, y, right_w, _style(6.6, MUTED))
    y -= 7 * mm

    # Observations + recommendations
    y = _section(c, "Key observations", M, y, accent)
    _section(c, "Recommendations", xr, y + 6.5 * mm, accent)
    ob_style = _style(7.4, INK, leading=9.9)
    yl = y
    for o in a.observations[:8]:
        yl -= _para(c, f"<font color='{accent}'>&bull;</font>&nbsp; {escape(o)}", M, yl, left_w, ob_style) + 1.6 * mm
    yr = y
    for i, r in enumerate(a.recommendations, 1):
        yr -= _para(c, f"<b><font color='{accent}'>{i}.</font></b>&nbsp; {escape(r)}", xr, yr, right_w,
                    ob_style) + 1.6 * mm

    _footer(c, 2, brand, generated)
    c.save()
    return buf.getvalue()
