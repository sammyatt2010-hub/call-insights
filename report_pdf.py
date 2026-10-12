"""
report_pdf.py - the 2-page A4 "Call Handling Assessment" PDF, styled to match the web portal.

Page 1: hero, KPI tiles, executive summary, when calls arrive, missed calls vs reality, follow-up.
Page 2: busiest times heatmap, lines & branches, callers never spoken to, observations, recommendations.

Two looks share one layout:
  "portal" - the dark midnight design of the web app (default)
  "print"  - the same layout on white, for printing
"""
from __future__ import annotations

import io
import os
from datetime import datetime
from xml.sax.saxutils import escape

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker  # noqa: F401
import numpy as np
from matplotlib import font_manager
from matplotlib.colors import LinearSegmentedColormap
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, Table, TableStyle

from cdr_engine import PROVIDERS, Analysis, fmt_dur, fmt_mins, pretty_number

W, H = A4
M = 12 * mm  # page margin
CW = W - 2 * M

# --------------------------------------------------------------------------- #
# Fonts - Inter, bundled in ./fonts so it works on Streamlit Cloud too
# --------------------------------------------------------------------------- #
FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
F = {"r": "Helvetica", "m": "Helvetica", "sb": "Helvetica-Bold", "b": "Helvetica-Bold", "xb": "Helvetica-Bold"}
try:
    for key, w in (("r", "Regular"), ("m", "Medium"), ("sb", "SemiBold"), ("b", "Bold"), ("xb", "ExtraBold")):
        path = os.path.join(FONT_DIR, f"Inter-{w}.ttf")
        pdfmetrics.registerFont(TTFont(f"Inter-{w}", path))
        font_manager.fontManager.addfont(path)
        F[key] = f"Inter-{w}"
    pdfmetrics.registerFontFamily("Inter", normal=F["r"], bold=F["b"], italic=F["r"], boldItalic=F["b"])
    plt.rcParams["font.family"] = "Inter"
except Exception:  # noqa: BLE001 - fall back to Helvetica if fonts are missing
    pass

# --------------------------------------------------------------------------- #
# Design tokens (same values as the web portal's CSS)
# --------------------------------------------------------------------------- #
THEMES = {
    "portal": dict(
        bg="#0A0E1A", surface="#111827", surface2="#161F33", surface3="#1C2740", card="#131B2D",
        border="#232D41", border_strong="#323D52", text="#E7EAF3", muted="#8C98B0", faint="#5E6A82",
        accent="#7C83FF", accent2="#38D6F5", accent_soft="#1E2342", good="#34D399", warn="#FBBF24",
        risk="#FB923C", bad="#F87171", good_soft="#123129", bad_soft="#33191F", glow=True,
        chart_text="#C7CEDD", grid="#1C2537", heat=["#111827", "#4F55C9", "#38D6F5"], heat_text_dark="#0A0E1A",
    ),
    "print": dict(
        bg="#FFFFFF", surface="#F6F7FB", surface2="#F1F3F9", surface3="#E9ECF5", card="#FFFFFF",
        border="#E3E7EF", border_strong="#CBD2DF", text="#0F172A", muted="#5B6478", faint="#8C95A8",
        accent="#5B62E8", accent2="#0EA5C6", accent_soft="#ECEDFE", good="#059669", warn="#D97706",
        risk="#EA580C", bad="#DC2626", good_soft="#E7F7F0", bad_soft="#FDECEC", glow=False,
        chart_text="#475569", grid="#EEF1F6", heat=["#F3F5FB", "#7C83FF", "#0EA5C6"], heat_text_dark="#FFFFFF",
    ),
}


def _c(h):
    return colors.HexColor(h)


class Theme:
    def __init__(self, name: str, accent: str | None):
        t = dict(THEMES.get(name, THEMES["portal"]))
        if accent and accent.lower() != THEMES["portal"]["accent"].lower():  # a custom brand colour
            t["accent"] = accent
        self.__dict__.update(t)
        self.name = name


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _style(T, size=8.5, color=None, font=None, leading=None):
    return ParagraphStyle("x", fontName=font or F["r"], fontSize=size, leading=leading or size * 1.42,
                          textColor=_c(color or T.text))


def _para_h(text, width, style):
    p = Paragraph(text, style)
    return p.wrap(width, 2000)[1]


def _para(c, text, x, y_top, width, style):
    p = Paragraph(text, style)
    _, h = p.wrap(width, 2000)
    p.drawOn(c, x, y_top - h)
    return h


def _round(c, x, y, w, h, r, fill=None, stroke=None, width=0.6):
    if fill:
        c.setFillColor(_c(fill))
    if stroke:
        c.setStrokeColor(_c(stroke))
        c.setLineWidth(width)
    c.roundRect(x, y, w, h, r, stroke=1 if stroke else 0, fill=1 if fill else 0)


def _gradient_round(c, x, y, w, h, r, c1, c2):
    c.saveState()
    p = c.beginPath()
    p.roundRect(x, y, w, h, r)
    c.clipPath(p, stroke=0, fill=0)
    c.linearGradient(x, y + h, x + w, y, (_c(c1), _c(c2)), extend=True)
    c.restoreState()


def _trunc(s, n):
    s = str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


def _fig_to_img(fig, T):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=220, bbox_inches="tight", facecolor=T.card)
    plt.close(fig)
    buf.seek(0)
    return ImageReader(buf)


def _setup_ax(ax, T):
    ax.set_facecolor(T.card)
    for sp in ("top", "right", "left"):
        ax.spines[sp].set_visible(False)
    ax.spines["bottom"].set_color(T.border_strong)
    ax.tick_params(colors=T.chart_text, labelsize=7, length=0)
    ax.grid(axis="y", color=T.grid, linewidth=0.8)
    ax.set_axisbelow(True)


def _legend(ax, T, **kw):
    leg = ax.legend(fontsize=6.6, frameon=False, labelcolor=T.chart_text, handlelength=0.9, handleheight=0.9, **kw)
    return leg


# --------------------------------------------------------------------------- #
# Charts
# --------------------------------------------------------------------------- #
def chart_hourly(a: Analysis, T, w_mm, h_mm):
    inb = a.inbound[~a.inbound["outcome"].isin(["Short abandon", "Failed"])]
    hrs = inb.assign(h=inb["ts"].dt.hour)
    tab = hrs.pivot_table(index="h", columns="outcome", values="call_id", aggfunc="count", fill_value=0)
    active = tab.sum(axis=1)
    sig = active[active >= max(1, active.sum() * 0.003)]
    lo, hi = (int(sig.index.min()), int(sig.index.max())) if len(sig) else (8, 18)
    lo, hi = max(0, min(lo, 8)), min(23, max(hi, 17))
    tab = tab.reindex(range(lo, hi + 1), fill_value=0)
    days = max(1, a.kpis["days"])
    get = lambda col: tab[col].values / days if col in tab else np.zeros(len(tab))  # noqa: E731
    if a.caps.get("routing", True):
        series = [("Answered", get("Answered"), T.accent), ("Answered via overflow", get("Answered via overflow"), T.accent2),
                  ("Missed", get("Missed"), T.bad), ("Engaged (busy)", get("Busy"), T.risk),
                  ("Voicemail", get("Voicemail"), T.warn)]
    else:
        series = [("Answered", get("Answered"), T.accent), ("Missed", get("Missed"), T.bad),
                  ("Engaged (busy)", get("Busy"), T.risk)]
    series = [s_ for s_ in series if s_[1].sum() > 0 or s_[0] == "Answered"]

    fig, ax = plt.subplots(figsize=(w_mm / 25.4, h_mm / 25.4))
    fig.patch.set_facecolor(T.card)
    x = np.arange(len(tab))
    bottom = np.zeros(len(tab))
    for label, vals, col in series:
        ax.bar(x, vals, bottom=bottom, color=col, width=0.68, label=label, linewidth=0)
        bottom = bottom + vals
    ax.set_xticks(x)
    ax.set_xticklabels([f"{h:02d}:00" for h in tab.index])
    _setup_ax(ax, T)
    _legend(ax, T, ncol=len(series), loc="lower left", bbox_to_anchor=(0, 1.0))
    fig.tight_layout()
    return _fig_to_img(fig, T)


def chart_reality(a: Analysis, T, w_mm, h_mm):
    k = a.kpis
    if a.caps.get("routing", True):
        labels = ["Phone system log\n'not answered'", "Truly missed\n(after stitching)", "Never returned\n(callers)"]
        vals = [k["pbx_reported_missed"], k["true_missed"], k["unreturned_callers"]]
    else:
        labels = ["Missed calls\n(incl. engaged)", "Missed calls\nnot resolved", "Never returned\n(callers)"]
        vals = [k["true_missed"], k["unreturned_calls"], k["unreturned_callers"]]
    cols = [T.faint, T.bad, "#B91C1C" if T.name == "print" else "#E05555"]
    fig, ax = plt.subplots(figsize=(w_mm / 25.4, h_mm / 25.4))
    fig.patch.set_facecolor(T.card)
    ax.set_facecolor(T.card)
    bars = ax.barh(labels[::-1], vals[::-1], color=cols[::-1], height=0.56, linewidth=0)
    top = max(vals) if max(vals) else 1
    for b, v in zip(bars, vals[::-1]):
        ax.text(b.get_width() + top * 0.03, b.get_y() + b.get_height() / 2, f"{v:,}", va="center",
                fontsize=9, color=T.text, fontweight="bold")
    ax.set_xlim(0, top * 1.22)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.set_xticks([])
    ax.tick_params(axis="y", labelsize=7, colors=T.chart_text, length=0)
    fig.tight_layout()
    return _fig_to_img(fig, T)


def chart_followup(a: Analysis, T, w_mm, h_mm):
    m = a.missed
    order = ["Called back - spoke", "Customer rang again - answered", "Call-back attempted - no answer",
             "Not returned", "Withheld number", "Too recent to judge"]
    cmap = {"Called back - spoke": T.good, "Customer rang again - answered": T.accent2,
            "Call-back attempted - no answer": T.warn, "Not returned": T.bad,
            "Withheld number": T.faint, "Too recent to judge": T.border_strong}
    counts = m["follow_up"].value_counts().reindex(order).fillna(0)
    counts = counts[counts > 0]
    fig, ax = plt.subplots(figsize=(w_mm / 25.4, h_mm / 25.4))
    fig.patch.set_facecolor(T.card)
    ax.set_facecolor(T.card)
    if counts.sum() == 0:
        ax.text(0.5, 0.5, "No missed calls", ha="center", va="center", color=T.muted)
        ax.axis("off")
        return _fig_to_img(fig, T)
    wedges, _ = ax.pie(counts.values, colors=[cmap[i] for i in counts.index], startangle=90, counterclock=False,
                       wedgeprops=dict(width=0.34, edgecolor=T.card, linewidth=2))
    ax.text(0, 0.1, f"{int(counts.sum()):,}", ha="center", va="center", fontsize=14, fontweight="bold", color=T.text)
    ax.text(0, -0.2, "missed calls", ha="center", va="center", fontsize=6.5, color=T.muted)
    _legend(ax, T, handles=wedges, labels=[f"{i}  ·  {int(v):,}" for i, v in counts.items()],
            loc="center left", bbox_to_anchor=(1.0, 0.5))
    ax.set_aspect("equal")
    fig.tight_layout()
    return _fig_to_img(fig, T)


def chart_heat(a: Analysis, T, w_mm, h_mm):
    heat = a.heat.copy()
    tot = heat.values.sum()
    cols = [h for h in heat.columns if heat[h].sum() >= max(1, tot * 0.003)] or list(range(8, 18))
    lo, hi = min(min(cols), 8), max(max(cols), 17)
    heat = heat.loc[:, lo:hi]
    rows = [d for d in range(7) if heat.loc[d].sum() > 0] or list(range(5))
    heat = heat.loc[rows]
    weeks = max(1, a.kpis["days"] / 7)
    vals = heat.values / weeks
    cm = LinearSegmentedColormap.from_list("portal", T.heat)
    fig, ax = plt.subplots(figsize=(w_mm / 25.4, h_mm / 25.4))
    fig.patch.set_facecolor(T.card)
    vmax = vals.max() if vals.size and vals.max() > 0 else 1
    # rounded-looking cells: draw each as a small square with a gap
    for i in range(vals.shape[0]):
        for j in range(vals.shape[1]):
            v = vals[i, j]
            ax.add_patch(plt.Rectangle((j - 0.46, i - 0.42), 0.92, 0.84, color=cm(v / vmax), linewidth=0))
            if round(v) >= 1:
                ax.text(j, i, f"{round(v):.0f}", ha="center", va="center", fontsize=6,
                        color=T.heat_text_dark if v > vmax * 0.6 else T.text)
    ax.set_xlim(-0.5, vals.shape[1] - 0.5)
    ax.set_ylim(vals.shape[0] - 0.5, -0.5)
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([names[d] for d in rows])
    ax.set_xticks(range(vals.shape[1]))
    ax.set_xticklabels([f"{h:02d}:00" for h in heat.columns])
    ax.set_facecolor(T.card)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.tick_params(length=0, colors=T.chart_text, labelsize=6.8)
    fig.tight_layout()
    return _fig_to_img(fig, T)


# --------------------------------------------------------------------------- #
# Page furniture
# --------------------------------------------------------------------------- #
def _page_bg(c, T):
    c.setFillColor(_c(T.bg))
    c.rect(0, 0, W, H, stroke=0, fill=1)
    if T.glow:  # the portal's soft indigo / cyan glow behind the hero
        c.saveState()
        steps = 36
        for cx, cy, r, col, alpha in ((W * 0.86, H + 5 * mm, 125 * mm, T.accent2, 0.06),
                                      (W * 0.10, H + 25 * mm, 115 * mm, T.accent, 0.09)):
            c.setFillColor(_c(col))
            c.setFillAlpha(alpha / steps * 1.6)
            for step in range(steps):
                c.circle(cx, cy, r * (1 - step / steps) ** 0.9, stroke=0, fill=1)
        c.restoreState()


def _phone_icon(c, x, y, s, col):
    """Feather 'phone' glyph drawn as a path (s = size of the 24-unit box)."""
    k = s / 24
    c.saveState()
    c.setStrokeColor(_c(col))
    c.setLineWidth(2 * k)
    c.setLineCap(1)
    c.setLineJoin(1)
    p = c.beginPath()
    pts = [(22, 16.9), (22, 19.9), (19.8, 21.9), (11.2, 18.8), (5.2, 12.8), (2.1, 4.1), (4.1, 2), (7.1, 2),
           (9.1, 3.7), (9.8, 6.5), (9.4, 8.6), (8.1, 9.9), (14.1, 15.9), (15.4, 14.6), (17.5, 14.2), (20.3, 14.9),
           (22, 16.9)]
    p.moveTo(x + pts[0][0] * k, y + (24 - pts[0][1]) * k)
    for px, py in pts[1:]:
        p.lineTo(x + px * k, y + (24 - py) * k)
    c.drawPath(p, stroke=1, fill=0)
    c.restoreState()


def _hero(c, T, customer, period, brand, provider, logo, eyebrow="CALL HANDLING ASSESSMENT"):
    top = H - 12 * mm
    # brand tile (or the uploaded logo) on the right
    if logo is not None:
        try:
            img = ImageReader(io.BytesIO(logo))
            iw, ih = img.getSize()
            lh = 14 * mm
            lw = min(46 * mm, lh * iw / ih)
            c.drawImage(img, W - M - lw, top - lh - 1 * mm, lw, lh, mask="auto", preserveAspectRatio=True)
        except Exception:  # noqa: BLE001
            logo = None
    if logo is None:
        s = 13 * mm
        _gradient_round(c, W - M - s, top - s - 1 * mm, s, s, 3.6 * mm, T.accent, T.accent2)
        _phone_icon(c, W - M - s + s * 0.24, top - s - 1 * mm + s * 0.24, s * 0.52, T.bg)

    # eyebrow: green dot + label
    c.setFillColor(_c(T.good))
    c.setFillAlpha(0.18)
    c.circle(M + 1.3 * mm, top - 1.6 * mm, 2.2 * mm, stroke=0, fill=1)
    c.setFillAlpha(1)
    c.circle(M + 1.3 * mm, top - 1.6 * mm, 1.15 * mm, stroke=0, fill=1)
    c.setFillColor(_c(T.accent2))
    c.setFont(F["b"], 6.8)
    c.drawString(M + 5 * mm, top - 2.6 * mm, eyebrow)

    # title: customer name with the last word in the accent colour (solid - gradient text prints badly)
    name = (customer or "Call handling assessment").strip()
    parts = name.rsplit(" ", 1)
    size = 22 if len(name) < 34 else 18
    c.setFont(F["xb"], size)
    c.setFillColor(_c(T.text))
    ty = top - 12.5 * mm
    if len(parts) == 2:
        c.drawString(M, ty, parts[0] + " ")
        x2 = M + pdfmetrics.stringWidth(parts[0] + " ", F["xb"], size)
        c.setFillColor(_c(T.accent))
        c.drawString(x2, ty, parts[1])
    else:
        c.setFillColor(_c(T.accent))
        c.drawString(M, ty, name)

    # meta chips
    chips = [period, PROVIDERS.get(provider, provider), f"Prepared by {brand}"]
    x = M
    cy = top - 21.5 * mm
    for i, txt in enumerate(chips):
        tw = pdfmetrics.stringWidth(txt, F["sb"], 6.8) + 7 * mm
        fill, fg, br = (T.accent_soft, T.accent if T.name == "print" else "#B9BDFF", T.accent) if i == 0 else \
            (T.surface3 if T.name == "portal" else T.surface2, T.text, T.border)
        _round(c, x, cy, tw, 5.4 * mm, 2.7 * mm, fill=fill, stroke=br if i else None, width=0.5)
        c.setFillColor(_c(fg))
        c.setFont(F["sb"], 6.8)
        c.drawString(x + 3.5 * mm, cy + 1.75 * mm, txt)
        x += tw + 2 * mm

    y = top - 26.5 * mm
    c.setStrokeColor(_c(T.border))
    c.setLineWidth(0.6)
    c.line(M, y, W - M, y)
    return y - 5 * mm


def _footer(c, T, page, brand, generated, total=2, doc="Call Handling Assessment"):
    c.setStrokeColor(_c(T.border))
    c.setLineWidth(0.5)
    c.line(M, 10 * mm, W - M, 10 * mm)
    c.setFillColor(_c(T.faint))
    c.setFont(F["m"], 6.3)
    c.drawString(M, 6.5 * mm, f"{brand}  ·  {doc}  ·  generated {generated}")
    c.drawRightString(W - M, 6.5 * mm, f"Page {page} of {total}")


def _card(c, T, x, y_top, w, h):
    """Card surface with border - portal `.st-key-card-*`."""
    _round(c, x, y_top - h, w, h, 3.6 * mm, fill=T.card, stroke=T.border, width=0.6)


def _section(c, T, num, title, sub, x, y_top, w=None):
    """Numbered badge + title + subtitle - portal `.pe-section`. Returns y below it."""
    b = 7.6 * mm
    _round(c, x, y_top - b, b, b, 2.2 * mm, fill=T.accent_soft, stroke=T.accent, width=0.4)
    c.setFillColor(_c(T.accent))
    c.setFont(F["xb"], 7.4)
    c.drawCentredString(x + b / 2, y_top - b / 2 - 2.6, num)
    c.setFillColor(_c(T.text))
    c.setFont(F["b"], 9.6)
    c.drawString(x + b + 3 * mm, y_top - 3.2 * mm, title)
    if sub:
        c.setFillColor(_c(T.muted))
        c.setFont(F["r"], 6.8)
        c.drawString(x + b + 3 * mm, y_top - 6.6 * mm, sub)
    return y_top - b - 3.5 * mm


def _kpi(c, T, x, y, w, h, label, value, sub, tone):
    _round(c, x, y - h, w, h, 3 * mm, fill=T.surface, stroke=T.border, width=0.6)
    # coloured top edge (2px in the portal)
    c.saveState()
    p = c.beginPath()
    p.roundRect(x, y - h, w, h, 3 * mm)
    c.clipPath(p, stroke=0, fill=0)
    c.setFillColor(_c(tone))
    c.rect(x, y - 0.9 * mm, w, 0.9 * mm, stroke=0, fill=1)
    c.restoreState()
    c.setFillColor(_c(T.muted))
    c.setFont(F["b"], 5.6)
    c.drawString(x + 3 * mm, y - 6 * mm, label.upper())
    c.setFillColor(_c(T.text))
    c.setFont(F["xb"], 15.5)
    c.drawString(x + 3 * mm, y - 13.6 * mm, value)
    c.setFillColor(_c(T.muted))
    c.setFont(F["r"], 6)
    c.drawString(x + 3 * mm, y - 17.8 * mm, _trunc(sub, 30))


def _table(c, T, data, x, y_top, col_w, tone_col=None, align_right_from=1, font=6.7):
    t = Table(data, colWidths=col_w, rowHeights=[5.6 * mm] + [5.0 * mm] * (len(data) - 1))
    st = [
        ("FONT", (0, 0), (-1, 0), F["b"], 5.8),
        ("FONT", (0, 1), (-1, -1), F["r"], font),
        ("TEXTCOLOR", (0, 0), (-1, 0), _c(T.muted)),
        ("BACKGROUND", (0, 0), (-1, 0), _c(T.surface3 if T.name == "portal" else T.surface2)),
        ("TEXTCOLOR", (0, 1), (-1, -1), _c(T.text)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (align_right_from, 0), (-1, -1), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, _c(T.border)),
        ("ROUNDEDCORNERS", [4, 4, 0, 0]),
    ]
    if tone_col is not None:
        for r, tone in tone_col.items():
            st.append(("TEXTCOLOR", (r[1], r[0]), (r[1], r[0]), _c(tone)))
            st.append(("FONT", (r[1], r[0]), (r[1], r[0]), F["b"], font))
    t.setStyle(TableStyle(st))
    _, h = t.wrap(sum(col_w), 1000)
    t.drawOn(c, x, y_top - h)
    return h


def _list_block(c, T, items, x, y_top, w, numbered, size):
    """Portal `.pe-list`: small badge + text, hairline between rows."""
    y = y_top
    st = _style(T, size, T.text, leading=size * 1.42)
    for i, txt in enumerate(items, 1):
        h = _para_h(escape(txt), w - 8 * mm, st)
        b = 4.4 * mm
        fill, fg = (T.good_soft, T.good) if numbered else (T.accent_soft, T.accent)
        _round(c, x, y - b - 0.4 * mm, b, b, 1.3 * mm, fill=fill)
        c.setFillColor(_c(fg))
        c.setFont(F["xb"], 6.2)
        c.drawCentredString(x + b / 2, y - b / 2 - 2.3, str(i) if numbered else "•")
        _para(c, escape(txt), x + 7 * mm, y, w - 8 * mm, st)
        y -= max(h, b) + 1.6 * mm
        if i < len(items):
            c.setStrokeColor(_c(T.border))
            c.setLineWidth(0.4)
            c.line(x, y + 0.8 * mm, x + w, y + 0.8 * mm)
            y -= 1.4 * mm
    return y_top - y


def _list_height(T, items, w, size):
    st = _style(T, size, T.text, leading=size * 1.42)
    h = 0
    for i, txt in enumerate(items, 1):
        h += max(_para_h(escape(txt), w - 8 * mm, st), 4.4 * mm) + 1.6 * mm + (1.4 * mm if i < len(items) else 0)
    return h


def _team_rows(a: Analysis):
    """Per person/extension: inbound calls answered, outbound calls, missed calls returned."""
    import pandas as pd

    ans = a.inbound[a.inbound["outcome"].isin(["Answered", "Answered via overflow"])]
    answered = ans[ans["answered_by"] != ""].groupby("answered_by").size()
    team = a.team
    names = sorted(set(answered.index) | set(team.index))
    if not names:
        return None
    df = pd.DataFrame(index=names)
    df["answered"] = answered.reindex(names).fillna(0).astype(int)
    df["outbound"] = team["outbound"].reindex(names).fillna(0).astype(int) if len(team) else 0
    df["returned"] = team["missed_calls_returned"].reindex(names).fillna(0).astype(int) if len(team) else 0
    df = df.sort_values(["answered", "outbound"], ascending=False)
    return [[_trunc(n, 28), f"{r.answered:,}", f"{r.outbound:,}", f"{r.returned:,}"] for n, r in df.iterrows()]


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def build_pdf(a: Analysis, customer: str = "", brand: str = "SY Communications", accent: str | None = None,
              logo: bytes | None = None, style: str = "portal") -> bytes:
    T = Theme(style, accent)
    k, s = a.kpis, a.settings
    routing, ring = a.caps.get("routing", True), a.caps.get("ring_time", True)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"Call Handling Assessment - {customer}".strip(" -"))
    c.setAuthor(brand)
    period = f"{a.period_start:%d %b %Y} → {a.period_end:%d %b %Y}"
    generated = datetime.now().strftime("%d %b %Y")
    gap = 4 * mm
    pad = 4.5 * mm

    # ================= PAGE 1 =================
    _page_bg(c, T)
    y = _hero(c, T, customer, period, brand, a.provider, logo)

    ar = k["answer_rate_ih"]
    ar_tone = T.good if ar >= s.answer_target_pct else (T.warn if ar >= s.answer_target_pct - 10 else T.bad)
    tiles = [
        ("Inbound calls", f"{k['inbound_total']:,}", f"{k['avg_inbound_per_open_day']:.0f} per working day", T.accent),
        ("Answered in hours", f"{ar:.1f}%", f"target {s.answer_target_pct:.0f}%", ar_tone),
        ("Truly missed", f"{k['true_missed']:,}", f"PBX log said {k['pbx_reported_missed']:,}", T.bad)
        if routing else ("Missed calls", f"{k['true_missed']:,}", f"{100 - k['answer_rate']:.1f}% of all inbound", T.bad),
        ("Saved by overflow", f"{k['answered_overflow']:,}",
         f"{100 * k['answered_overflow'] / max(1, k['answered']):.0f}% of answered", T.accent2)
        if routing else ("Engaged / busy", f"{k['busy']:,}", "callers got a busy tone", T.warn if k["busy"] else T.good),
        ("Un-returned callers", f"{k['unreturned_callers']:,}", f"{k['unreturned_calls']:,} missed calls",
         T.bad if k["unreturned_callers"] else T.good),
        ("Avg time to answer", fmt_dur(k["avg_ring_s"]), f"avg talk {fmt_dur(k['avg_talk_s'])}", T.accent)
        if ring else ("Avg call length", fmt_dur(k["avg_talk_s"]), "per answered call", T.accent),
    ]
    tg = 2.6 * mm
    tw = (CW - tg * 5) / 6
    for i, (l_, v, sub, tone) in enumerate(tiles):
        _kpi(c, T, M + i * (tw + tg), y, tw, 21 * mm, l_, v, sub, tone)
    y -= 21 * mm + gap

    # 01 Executive summary
    sum_st = _style(T, 8.4, T.text, leading=12.6)
    sh = _para_h(escape(a.summary), CW - 2 * pad - 3 * mm, sum_st)
    ch = 7.6 * mm + 3.5 * mm + sh + 2 * pad + 2 * mm
    _card(c, T, M, y, CW, ch)
    yy = _section(c, T, "01", "Executive summary", "", M + pad, y - pad)
    c.setFillColor(_c(T.accent2))
    c.rect(M + pad, yy - sh - 1 * mm, 0.9 * mm, sh + 1 * mm, stroke=0, fill=1)
    _para(c, escape(a.summary), M + pad + 3 * mm, yy - 0.5 * mm, CW - 2 * pad - 3 * mm, sum_st)
    y -= ch + gap

    # 02 When calls arrive
    chart_h = 58
    ch = 7.6 * mm + 3.5 * mm + chart_h * mm + 2 * pad
    _card(c, T, M, y, CW, ch)
    yy = _section(c, T, "02", "When calls arrive", "Average calls per day, by hour of the day", M + pad, y - pad)
    c.drawImage(chart_hourly(a, T, (CW - 2 * pad) / mm, chart_h), M + pad, yy - chart_h * mm, CW - 2 * pad,
                chart_h * mm)
    y -= ch + gap

    # 03 Missed vs reality | 04 What happened to missed callers
    half = (CW - gap) / 2
    chart2 = 44
    ch = 7.6 * mm + 3.5 * mm + chart2 * mm + 2 * pad
    _card(c, T, M, y, half, ch)
    _card(c, T, M + half + gap, y, half, ch)
    if routing:
        yy = _section(c, T, "03", "Missed calls: log vs reality", "Before and after stitching call legs", M + pad,
                      y - pad)
    else:
        yy = _section(c, T, "03", "Missed calls resolved?", "Missed, unresolved and never spoken to", M + pad, y - pad)
    _section(c, T, "04", "What happened to missed callers", "Follow-up after each missed call",
             M + half + gap + pad, y - pad)
    iw = half - 2 * pad
    c.drawImage(chart_reality(a, T, iw / mm, chart2), M + pad, yy - chart2 * mm, iw, chart2 * mm)
    c.drawImage(chart_followup(a, T, iw / mm, chart2), M + half + gap + pad, yy - chart2 * mm, iw, chart2 * mm)
    y -= ch + 3 * mm

    if routing:
        note = (f"<b>How to read this:</b> the phone system writes a separate entry for every ring group, overflow "
                f"group, menu and voicemail a call passes through, so one caller can appear as several 'not answered' "
                f"rows. These have been stitched back into {k['inbound_total']:,} real calls. A call only counts as "
                f"missed if no person answered it at any stage. Opening hours used: "
                f"{s.open_time:%H:%M}-{s.close_time:%H:%M}. Callers who hung up within {s.short_abandon_s}s are "
                f"excluded.")
    else:
        note = (f"<b>How to read this:</b> this report is built from the line-level call log, which records one entry "
                f"per call: answered, missed (rang with no answer) or engaged (busy tone). Calls between the business's "
                f"own sites and call-forwarding legs have been removed. A missed call counts as followed up if the "
                f"caller was later called back and connected, or rang again and was answered. Opening hours used: "
                f"{s.open_time:%H:%M}-{s.close_time:%H:%M}.")
    _para(c, note, M, y, CW, _style(T, 6.4, T.faint, leading=8.8))
    _footer(c, T, 1, brand, generated)
    c.showPage()

    # ================= PAGE 2 =================
    _page_bg(c, T)
    y = _hero(c, T, customer, period, brand, a.provider, logo)

    # 05 Busiest times
    hh = 42
    ch = 7.6 * mm + 3.5 * mm + hh * mm + 2 * pad
    _card(c, T, M, y, CW, ch)
    yy = _section(c, T, "05", "Busiest times", "Average inbound calls per week, by day and hour", M + pad, y - pad)
    c.drawImage(chart_heat(a, T, (CW - 2 * pad) / mm, hh), M + pad, yy - hh * mm, CW - 2 * pad, hh * mm)
    y -= ch + gap

    # 06 Lines | 07 Callers never spoken to
    left_w = CW * 0.52
    right_w = CW - left_w - gap
    lines = a.lines.head(10)
    rows = [["LINE / BRANCH", "CALLS", "ANS %", "MISSED", "O'FLOW" if routing else "BUSY", "UNRET."]]
    tones = {}
    for ri, (name, r) in enumerate(lines.iterrows(), 1):
        rows.append([_trunc(name, 32), f"{int(r.calls):,}", f"{r.answer_rate:.0f}%", f"{int(r.missed):,}",
                     f"{int(r.overflow if routing else r.busy):,}", f"{int(r.unreturned_callers):,}"])
        tones[(ri, 2)] = T.good if r.answer_rate >= s.answer_target_pct else (
            T.warn if r.answer_rate >= s.answer_target_pct - 10 else T.bad)
        if r.unreturned_callers:
            tones[(ri, 5)] = T.bad
    ur = a.unreturned_callers.head(10)
    rows2 = [["CALLER", "MISSED", "LAST MISSED", "STATUS"]]
    for r in ur.itertuples():
        rows2.append([pretty_number(r.caller), str(int(r.missed_calls)), f"{r.last_missed:%d %b %H:%M}",
                      "Tried, no answer" if r.follow_up.startswith("Call-back") else "Not returned"])
    if len(rows2) == 1:
        rows2.append(["Every missed caller was followed up", "", "", ""])
    tbl_h = 5.6 * mm + 5.0 * mm * (max(len(rows), len(rows2)) - 1)
    extra = []
    if len(a.unreturned_callers) > 10:
        extra.append(f"+{len(a.unreturned_callers) - 10} more in the app's call-back list.")
    if k.get("median_callback_min") == k.get("median_callback_min"):
        extra.append(f"Median time to return a missed call: {fmt_mins(k['median_callback_min'])}.")
    ch = 7.6 * mm + 3.5 * mm + tbl_h + 2 * pad + (5 * mm if extra else 0)
    # team table fills the space under a short lines table
    team = _team_rows(a)
    spare_rows = int((tbl_h - (5.6 * mm + 5.0 * mm * (len(rows) - 1)) - 9 * mm) // (5.0 * mm)) - 1
    _card(c, T, M, y, left_w, ch)
    _card(c, T, M + left_w + gap, y, right_w, ch)
    yy = _section(c, T, "06", "Lines & branches", "Answer rate per number called", M + pad, y - pad)
    lw = [(left_w - 2 * pad) * f for f in (0.42, 0.11, 0.12, 0.12, 0.11, 0.12)]
    h_lines = _table(c, T, rows, M + pad, yy, lw, tone_col=tones)
    if team is not None and spare_rows >= 2:
        ty = yy - h_lines - 4 * mm
        c.setFillColor(_c(T.muted))
        c.setFont(F["b"], 6)
        c.drawString(M + pad, ty - 2.2 * mm, "TEAM · WHO ANSWERED AND RETURNED CALLS")
        trows = [["NAME", "ANSWERED", "OUTBOUND", "CALL-BACKS"]] + team[:spare_rows]
        tw_ = [(left_w - 2 * pad) * f for f in (0.43, 0.19, 0.19, 0.19)]
        _table(c, T, trows, M + pad, ty - 4.5 * mm, tw_)
    xr = M + left_w + gap
    _section(c, T, "07", "Callers never spoken to", "Missed, and no conversation since", xr + pad, y - pad)
    rw = [(right_w - 2 * pad) * f for f in (0.33, 0.15, 0.26, 0.26)]
    _table(c, T, rows2, xr + pad, yy, rw, tone_col={(i, 3): T.bad for i in range(1, len(rows2))})
    if extra:
        c.setFillColor(_c(T.muted))
        c.setFont(F["r"], 6.2)
        c.drawString(xr + pad, y - ch + pad - 0.5 * mm, " ".join(extra))
    y -= ch + gap

    # 08 Observations | 09 Recommendations - shrink text until it fits the page
    avail = y - 13 * mm
    obs, recs = a.observations[:8], a.recommendations[:7]
    def need_for(sz):
        return max(_list_height(T, obs, half - 2 * pad, sz), _list_height(T, recs, half - 2 * pad, sz))

    while True:
        for size in (7.4, 7.1, 6.8, 6.5, 6.2):
            need = need_for(size)
            if 7.6 * mm + 3.5 * mm + need + 2 * pad <= avail:
                break
        else:
            # still too long: drop the least important (last) item from the longer list and retry
            if len(obs) >= len(recs) and len(obs) > 3:
                obs = obs[:-1]
            elif len(recs) > 3:
                recs = recs[:-1]
            else:
                break
            continue
        break
    ch = min(avail, 7.6 * mm + 3.5 * mm + need + 2 * pad)
    _card(c, T, M, y, half, ch)
    _card(c, T, M + half + gap, y, half, ch)
    yy = _section(c, T, "08", "Key observations", "", M + pad, y - pad)
    _section(c, T, "09", "Recommendations", "", M + half + gap + pad, y - pad)
    _list_block(c, T, obs, M + pad, yy, half - 2 * pad, False, size)
    _list_block(c, T, recs, M + half + gap + pad, yy, half - 2 * pad, True, size)

    _footer(c, T, 2, brand, generated)
    c.save()
    return buf.getvalue()


# =========================================================================== #
# User assessment PDF
# =========================================================================== #
def chart_workload(rep, T, w_mm, h_mm, limit=22):
    t = rep.table.head(limit).iloc[::-1]
    fig, ax = plt.subplots(figsize=(w_mm / 25.4, h_mm / 25.4))
    fig.patch.set_facecolor(T.card)
    ax.set_facecolor(T.card)
    y = np.arange(len(t))
    ax.barh(y, t["answered"], color=T.accent, height=0.62, label="Inbound answered", linewidth=0)
    ax.barh(y, t["outbound"], left=t["answered"], color=T.accent2, height=0.62, label="Outbound calls", linewidth=0)
    top = max(1, (t["answered"] + t["outbound"]).max())
    for yi, (a_, o_) in enumerate(zip(t["answered"], t["outbound"])):
        ax.text(a_ + o_ + top * 0.01, yi, f"{a_ + o_:,}", va="center", fontsize=6.3, color=T.chart_text)
    ax.set_yticks(y)
    ax.set_yticklabels([_trunc(n, 24) for n in t["name"]])
    ax.set_xlim(0, top * 1.1)
    for sp in ("top", "right", "left"):
        ax.spines[sp].set_visible(False)
    ax.spines["bottom"].set_color(T.border_strong)
    ax.tick_params(colors=T.chart_text, labelsize=6.6, length=0)
    ax.grid(axis="x", color=T.grid, linewidth=0.8)
    ax.set_axisbelow(True)
    _legend(ax, T, ncol=2, loc="lower left", bbox_to_anchor=(0, 1.0))
    fig.tight_layout()
    return _fig_to_img(fig, T)


def chart_user_hours(hp, T, w_mm, h_mm):
    hp = hp.loc[7:19]
    fig, ax = plt.subplots(figsize=(w_mm / 25.4, h_mm / 25.4))
    fig.patch.set_facecolor(T.card)
    x = np.arange(len(hp))
    ax.bar(x, hp["inbound"], color=T.accent, width=0.7, label="In", linewidth=0)
    ax.bar(x, hp["outbound"], bottom=hp["inbound"], color=T.accent2, width=0.7, label="Out", linewidth=0)
    ax.set_xticks(x[::2])
    ax.set_xticklabels([f"{h:02d}" for h in hp.index[::2]])
    _setup_ax(ax, T)
    ax.tick_params(labelsize=6)
    ax.yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(3, integer=True))
    _legend(ax, T, ncol=2, loc="lower left", bbox_to_anchor=(0, 0.98))
    fig.tight_layout()
    return _fig_to_img(fig, T)


def _chip(c, T, x, y, text, tone="plain", size=6.4):
    tw = pdfmetrics.stringWidth(text, F["sb"], size) + 5 * mm
    if tone == "accent":
        fill, fg, br = T.accent_soft, T.accent if T.name == "print" else "#B9BDFF", T.accent
    elif tone == "good":
        fill, fg, br = T.good_soft, T.good, T.good
    elif tone == "bad":
        fill, fg, br = T.bad_soft, T.bad, T.bad
    else:
        fill, fg, br = (T.surface3 if T.name == "portal" else T.surface2), T.text, T.border
    _round(c, x, y, tw, 4.8 * mm, 2.4 * mm, fill=fill, stroke=br, width=0.4)
    c.setFillColor(_c(fg))
    c.setFont(F["sb"], size)
    c.drawString(x + 2.5 * mm, y + 1.55 * mm, text)
    return tw


def _mini(c, T, x, y, w, label, value, sub=""):
    _round(c, x, y - 13 * mm, w, 13 * mm, 2.4 * mm, fill=T.surface, stroke=T.border, width=0.5)
    c.setFillColor(_c(T.muted))
    c.setFont(F["b"], 5.2)
    c.drawString(x + 2.4 * mm, y - 4.2 * mm, label.upper())
    c.setFillColor(_c(T.text))
    c.setFont(F["xb"], 11)
    c.drawString(x + 2.4 * mm, y - 9.4 * mm, value)
    if sub:
        c.setFillColor(_c(T.muted))
        c.setFont(F["r"], 5.4)
        c.drawString(x + 2.4 * mm, y - 12 * mm, _trunc(sub, 26))


def _compact_header(c, T, customer, period, page_title):
    top = H - 12 * mm
    c.setFillColor(_c(T.accent2))
    c.setFont(F["b"], 6.4)
    c.drawString(M, top - 2.4 * mm, "USER ASSESSMENT")
    c.setFillColor(_c(T.text))
    c.setFont(F["xb"], 13)
    c.drawString(M, top - 8.6 * mm, page_title)
    c.setFillColor(_c(T.muted))
    c.setFont(F["m"], 6.8)
    c.drawRightString(W - M, top - 2.4 * mm, customer or "")
    c.drawRightString(W - M, top - 8 * mm, period)
    c.setStrokeColor(_c(T.border))
    c.setLineWidth(0.6)
    c.line(M, top - 12 * mm, W - M, top - 12 * mm)
    return top - 16 * mm


def _user_card(c, T, rep, ext, r, x, y_top, w, h):
    _card(c, T, x, y_top, w, h)
    pad = 4.5 * mm
    full = rep.level == "full"
    name = _trunc(r["name"], 34)
    c.setFillColor(_c(T.text))
    c.setFont(F["xb"], 11.5)
    c.drawString(x + pad, y_top - pad - 4 * mm, name)
    cx = x + pad + pdfmetrics.stringWidth(name, F["xb"], 11.5) + 3 * mm
    cy = y_top - pad - 5 * mm
    if full:
        cx += _chip(c, T, cx, cy, f"Ext {ext}", "accent") + 1.5 * mm
        for g in [g for g in str(r["groups"]).split(", ") if g][:3]:
            cx += _chip(c, T, cx, cy, f"Group {g}") + 1.5 * mm
    share = r["share_pct"]
    if share == share and share > 0:
        tag = f"{share:.0f}% of team's answered calls"
        tw = pdfmetrics.stringWidth(tag, F["sb"], 6.4) + 5 * mm
        _chip(c, T, x + w - pad - tw, cy, tag, "good" if share >= 10 else "plain")

    if full:
        stats = [("Rang them", f"{int(r['rang']):,}", f"{int(r['lost'])} lost while ringing"),
                 ("Answered", f"{int(r['answered']):,}",
                  f"pick-up {r['pickup_pct']:.0f}%" if r["pickup_pct"] == r["pickup_pct"] else ""),
                 ("Avg answer", fmt_dur(r["avg_ring_s"]), "time to pick up"),
                 ("Outbound", f"{int(r['outbound']):,}",
                  f"{r['connect_pct']:.0f}% connected" if r["connect_pct"] == r["connect_pct"] else ""),
                 ("Talk time", fmt_dur(r["talk_s"]), f"{int(r['internal_made'])} internal calls"),
                 ("Returned", f"{int(r['returned']):,}", "missed calls")]
    else:
        stats = [("Answered", f"{int(r['answered']):,}", ""), ("Avg answer", fmt_dur(r["avg_ring_s"]), ""),
                 ("Outbound", f"{int(r['outbound']):,}", ""),
                 ("Connected", f"{r['connect_pct']:.0f}%" if r["connect_pct"] == r["connect_pct"] else "-", ""),
                 ("Talk time", fmt_dur(r["talk_s"]), ""), ("Returned", f"{int(r['returned']):,}", "missed calls")]
    sg = 2 * mm
    sw = (w - 2 * pad - sg * 5) / 6
    sy = y_top - pad - 9 * mm
    for i, (l_, v, sub) in enumerate(stats):
        _mini(c, T, x + pad + i * (sw + sg), sy, sw, l_, v, sub)

    by = sy - 13 * mm - 3.5 * mm
    avail_h = by - (y_top - h) - pad
    lw_ = (w - 2 * pad) * 0.6
    obs = rep.observations.get(ext, [])
    size = 7.0
    for size in (7.0, 6.7, 6.4, 6.1, 5.8):
        if _list_height(T, obs, lw_, size) <= avail_h:
            break
    while obs and _list_height(T, obs, lw_, size) > avail_h:
        obs = obs[:-1]
    _list_block(c, T, obs, x + pad, by, lw_, False, size)
    cw_ = (w - 2 * pad) - lw_ - 4 * mm
    hp = rep.hourly.get(ext)
    if hp is not None and hp.values.sum() > 0:
        c.setFillColor(_c(T.muted))
        c.setFont(F["b"], 5.4)
        c.drawString(x + w - pad - cw_, by - 1.8 * mm, "CALLS BY HOUR")
        ch_h = avail_h - 4 * mm
        c.drawImage(chart_user_hours(hp, T, cw_ / mm, ch_h / mm), x + w - pad - cw_, by - 3 * mm - ch_h, cw_, ch_h)


def build_user_pdf(a: Analysis, rep, customer: str = "", brand: str = "SY Communications",
                   accent: str | None = None, logo: bytes | None = None, style: str = "portal") -> bytes:
    T = Theme(style, accent)
    t = rep.table
    tm = rep.team
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"User Assessment - {customer}".strip(" -"))
    c.setAuthor(brand)
    period = f"{a.period_start:%d %b %Y} → {a.period_end:%d %b %Y}"
    generated = datetime.now().strftime("%d %b %Y")
    gap, pad = 4 * mm, 4.5 * mm
    people = t[(t["handled"] > 0) | (t["rang"] >= 40)]
    per_page = 3
    total = 2 + int(np.ceil(len(people) / per_page))
    doc = "User Assessment"

    # ---------- page 1: team summary ----------
    _page_bg(c, T)
    y = _hero(c, T, customer, period, brand, a.provider, logo, eyebrow="USER ASSESSMENT")
    tiles = [
        ("People active", f"{tm['people']:,}", "made or answered calls", T.accent),
        ("Inbound answered", f"{tm['answered']:,}", f"top: {tm['top']}", T.good),
        ("Outbound calls", f"{tm['outbound']:,}",
         f"{tm['connect_pct']:.0f}% connected" if tm["connect_pct"] == tm["connect_pct"] else "", T.accent2),
        ("Avg time to answer", fmt_dur(tm["avg_ring_s"]), "across the team", T.accent),
        ("Talk time", fmt_dur(tm["talk_s"]), "inbound + outbound", T.accent),
        ("Missed calls returned", f"{tm['returned']:,}", f"{a.kpis['unreturned_callers']} callers still waiting",
         T.good if tm["returned"] else T.warn),
    ]
    tg = 2.6 * mm
    tw = (CW - tg * 5) / 6
    for i, (l_, v, sub, tone) in enumerate(tiles):
        _kpi(c, T, M + i * (tw + tg), y, tw, 21 * mm, l_, v, sub, tone)
    y -= 21 * mm + gap

    n = min(22, len(t))
    chart_h = max(40, 9 + 4.6 * n)
    ch = 7.6 * mm + 3.5 * mm + chart_h * mm + 2 * pad
    _card(c, T, M, y, CW, ch)
    yy = _section(c, T, "01", "Who handles the calls", "Inbound calls answered and outbound calls made, per person",
                  M + pad, y - pad)
    c.drawImage(chart_workload(rep, T, (CW - 2 * pad) / mm, chart_h), M + pad, yy - chart_h * mm, CW - 2 * pad,
                chart_h * mm)
    y -= ch + gap

    avail = y - 13 * mm
    obs = list(rep.team_observations)
    size = 7.6
    for size in (7.6, 7.2, 6.8, 6.4, 6.0):
        if 7.6 * mm + 3.5 * mm + _list_height(T, obs, CW - 2 * pad, size) + 2 * pad <= avail:
            break
    while obs and 7.6 * mm + 3.5 * mm + _list_height(T, obs, CW - 2 * pad, size) + 2 * pad > avail:
        obs = obs[:-1]
    ch = 7.6 * mm + 3.5 * mm + _list_height(T, obs, CW - 2 * pad, size) + 2 * pad
    _card(c, T, M, y, CW, ch)
    yy = _section(c, T, "02", "Team observations", "", M + pad, y - pad)
    _list_block(c, T, obs, M + pad, yy, CW - 2 * pad, False, size)
    _footer(c, T, 1, brand, generated, total, doc)
    c.showPage()

    # ---------- page 2: overview table ----------
    _page_bg(c, T)
    y = _compact_header(c, T, customer, period, "Team overview")
    full = rep.level == "full"
    if full:
        head = ["NAME", "EXT", "RANG", "ANSWERED", "PICK-UP", "AVG ANS.", "LOST", "OUTBOUND", "CONN.", "TALK",
                "RETURNED"]
        fr = (0.2, 0.06, 0.07, 0.09, 0.08, 0.08, 0.06, 0.09, 0.07, 0.1, 0.1)
    else:
        head = ["NAME", "ANSWERED", "SHARE", "AVG ANS.", "OUTBOUND", "CONN.", "TALK", "RETURNED"]
        fr = (0.28, 0.1, 0.09, 0.1, 0.1, 0.09, 0.12, 0.12)
    rows, tones = [head], {}
    max_rows = int((y - 40 * mm - 5.6 * mm) // (5.0 * mm))
    for i, (e, r) in enumerate(t.head(max_rows).iterrows(), 1):
        pick = f"{r['pickup_pct']:.0f}%" if r["pickup_pct"] == r["pickup_pct"] else "-"
        conn = f"{r['connect_pct']:.0f}%" if r["connect_pct"] == r["connect_pct"] else "-"
        if full:
            rows.append([_trunc(r["name"], 24), e, f"{int(r['rang']):,}", f"{int(r['answered']):,}", pick,
                         fmt_dur(r["avg_ring_s"]), f"{int(r['lost']):,}", f"{int(r['outbound']):,}", conn,
                         fmt_dur(r["talk_s"]), f"{int(r['returned']):,}"])
            if r["rang"] >= 40:
                p_ = r["pickup_pct"]
                tones[(i, 4)] = T.good if p_ >= 20 else (T.warn if p_ >= 5 else T.bad)
        else:
            rows.append([_trunc(r["name"], 30), f"{int(r['answered']):,}", f"{r['share_pct']:.0f}%",
                         fmt_dur(r["avg_ring_s"]), f"{int(r['outbound']):,}", conn, fmt_dur(r["talk_s"]),
                         f"{int(r['returned']):,}"])
    tbl_h = 5.6 * mm + 5.0 * mm * (len(rows) - 1)
    note = ("<b>Rang</b>: inbound calls that rang this person's handset. <b>Pick-up</b>: share of those they "
            "answered (ring groups ring several people at once, so a low figure is normal in a large group, but "
            "near zero means calls are ringing someone who never answers). <b>Lost</b>: calls that rang them and were "
            "never answered by anyone. <b>Returned</b>: missed calls they later rang back and spoke to the caller."
            if full else
            "Bicom exports show who answered and who dialled out, but not every handset that rang, so pick-up rate "
            "and calls lost while ringing aren't available.")
    nh = _para_h(note, CW - 2 * pad, _style(T, 6.4, T.muted, leading=8.8))
    ch = 7.6 * mm + 3.5 * mm + tbl_h + 2 * pad + nh + 3 * mm
    _card(c, T, M, y, CW, ch)
    yy = _section(c, T, "03", "Every person at a glance", f"{len(t)} extensions with activity in the period",
                  M + pad, y - pad)
    _table(c, T, rows, M + pad, yy, [(CW - 2 * pad) * f for f in fr], tone_col=tones, font=6.4)
    _para(c, note, M + pad, yy - tbl_h - 3 * mm, CW - 2 * pad, _style(T, 6.4, T.muted, leading=8.8))
    y -= ch + gap

    gt = getattr(rep, "group_table", None)
    if gt is not None and len(gt):
        grows = [["RING GROUP", "MEMBERS", "CALLS", "ANSWERED", "ANSWER %", "LOST", "PEOPLE ANSWERING",
                  "TOP ANSWERER"]]
        gtones = {}
        for i, g in enumerate(gt.sort_values("calls", ascending=False).itertuples(), 1):
            grows.append([f"Ring group {g.group}", f"{g.members}", f"{g.calls:,}", f"{g.answered:,}",
                          f"{g.answer_pct:.0f}%", f"{g.lost:,}", f"{g.answering_people} of {g.members}",
                          f"{_trunc(g.top, 18)} ({g.top_pct:.0f}%)" if g.top_pct == g.top_pct else "-"])
            gtones[(i, 4)] = T.good if g.answer_pct >= a.settings.answer_target_pct else T.warn
        gh = 5.6 * mm + 5.0 * mm * (len(grows) - 1)
        ch = 7.6 * mm + 3.5 * mm + gh + 2 * pad
        if y - ch > 13 * mm:
            _card(c, T, M, y, CW, ch)
            yy = _section(c, T, "04", "Ring groups", "How each group's calls were shared out", M + pad, y - pad)
            _table(c, T, grows, M + pad, yy, [(CW - 2 * pad) * f for f in (0.14, 0.09, 0.09, 0.1, 0.1, 0.08, 0.16,
                                                                             0.24)], tone_col=gtones, font=6.6)
    _footer(c, T, 2, brand, generated, total, doc)
    c.showPage()

    # ---------- pages 3+: a card per person ----------
    page = 3
    ids = list(people.index)
    for start_i in range(0, len(ids), per_page):
        _page_bg(c, T)
        y = _compact_header(c, T, customer, period, "Individual assessments")
        card_h = (y - 13 * mm - gap * (per_page - 1)) / per_page
        for e in ids[start_i:start_i + per_page]:
            _user_card(c, T, rep, e, t.loc[e], M, y, CW, card_h)
            y -= card_h + gap
        _footer(c, T, page, brand, generated, total, doc)
        c.showPage()
        page += 1
    c.save()
    return buf.getvalue()
