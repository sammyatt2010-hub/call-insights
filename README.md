# Call Insights – Call Handling Assessment

Upload a raw PBX call-record (CDR) export and get a true picture of call handling, plus a branded
2-page PDF for the customer.

## What it does

- **Stitches call legs back together.** The PBX writes a row for every ring group, overflow group,
  IVR menu and voicemail a call touches. One customer call can appear as several "Not Answered" rows.
  The app groups them into real calls (same unique-id, or the same caller within 3 seconds).
- **True missed calls.** A call only counts as missed if no person answered at any stage.
  Calls rescued by an overflow group are shown separately as *Answered via overflow*.
- **Un-returned missed calls.** For every missed call it looks forward through the whole file for
  (a) an outbound call to that number that connected, or (b) the customer ringing again and being answered.
  Anything else is *Not returned* (or *Call-back attempted – no answer*).
- **Busiest hours**, day × hour heatmap, per-line/branch answer rates, team call-backs.
- **Plain-English observations and recommendations**, generated from the numbers.
- **Periods:** whole file (auto-detected), this month, last month, last 30 / 7 days, or custom.
  Call-backs are still searched across the whole file, so a call missed on the 31st and returned on the 1st counts.

## Settings (sidebar)

Opening hours and days, answer-rate target, call-back target, "ignore hang-ups under X seconds",
excluding nuisance or international numbers, and PDF branding (prepared-by name, accent colour, logo).

## Input format

Built around the PBX export with these columns (no header, tab-separated, UTF-16):
`From, To, Start, Duration, Talk time, Cost, Status, Unique ID, Caller ID, MOS`.
Files with a header row, comma separators or UTF-8 also work. Several files can be uploaded together;
overlapping rows are removed.

Extensions are recognised as `Name (123)`; voicemail boxes as extensions whose name starts with `VM`.

## Known limits

- If a caller hangs up while still in the IVR menu, the PBX logs that as "Answered". Those calls count as answered.
- Each DID is labelled after the extension that most often uses it as outbound caller ID, or
  "IVR / main menu" if it fronts a menu.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy on Streamlit Community Cloud

1. Push this folder to a new GitHub repository (e.g. `call-insights`).
2. On share.streamlit.io choose **New app**, pick the repo, branch `main`, main file `app.py`.
3. Deploy. No secrets are needed. Uploaded files are processed in memory and not stored.

## Files

| File | Purpose |
|---|---|
| `app.py` | Streamlit interface |
| `cdr_engine.py` | Parsing, call stitching, missed-call tracking, metrics, insights |
| `report_pdf.py` | 2-page A4 PDF report |
| `.streamlit/config.toml` | Dark theme |
