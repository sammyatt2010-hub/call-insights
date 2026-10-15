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

## User assessment

A **User assessment** tab and a separate multi-page **User assessment PDF** (team overview, ring groups, then a
card per person with stats, observations and a calls-by-hour chart).

| Measure | Grandstream | Bicom / Vela | TruSIP |
|---|---|---|---|
| Inbound calls that rang each person, pick-up rate, calls lost while ringing | Yes | - | - |
| Inbound answered, share of team, time to answer, talk time | Yes | Yes | - |
| Outbound calls, connect rate, numbers dialled | Yes | Yes (per extension) | - |
| Internal calls, transfers, typical working hours | Yes | - | - |
| Missed calls each person returned | Yes | Yes | - |
| Ring-group performance (members, answer %, who carries the load) | Yes | - | - |

Observations are generated per person, e.g. a handset that rings on hundreds of calls but never answers
(check ring-group membership / Do Not Disturb), people carrying a large share of calls, slow or fast answering,
low outbound connect rates, frequent transfers, missed calls returned and typical hours.

## Settings (sidebar)

Opening hours and days, answer-rate target, call-back target, "ignore hang-ups under X seconds",
excluding nuisance or international numbers, and PDF branding (prepared-by name, accent colour, logo).

## Phone systems supported

Pick the phone system from the **Phone system** dropdown before uploading. If the file looks like the other
provider's export the app warns you.

| | Bicom | TruSIP | Grandstream UCM | Vela Networks |
|---|---|---|---|---|
| Export | CDR, tab-separated, UTF-16, no header | Call log CSV with header | CDR CSV(s); the `.tar`/`.zip` download can be uploaded as-is (no file extension needed) | Call log CSV (`IPPBXCE-Cdr-…csv`) |
| Rows | One per *leg* | One per call, line level | `main_cdr` per call + `sub_cdr` per leg, grouped by `Session` | One per call, listing IVR, ring groups and queue |
| Truly missed vs PBX log | Yes - legs stitched | Missed + engaged | Yes - compared with the UCM's own call status | Yes - incl. callers who hung up in the IVR menu (logged by the PBX as answered) |
| Overflow rescues | Yes | Not in the export | Yes (ring group -> ring group) | Yes (e.g. Tyres Ring Group -> Tyres Overflow) |
| Time to answer / quick hang-ups | Yes | Not in the export | Yes (from leg answer times) | Yes (routing duration, incl. menu time) |
| Who answered | Extension / queue | Not in the export | Named staff (names learnt from the extensions' own calls) | Ring group (not the person) |
| Call-back tracking & un-returned callers | Yes | Yes | Yes | Yes |
| Line names | Outbound caller ID / IVR detection | TruSIP "Called Comments" | The ring group each number rings (e.g. "Ring group 6402 · 01244 840083") | The ring group / IVR / voicemail the call reached |

TruSIP-specific clean-up:
- Calls between the business's own numbers (site to site) are removed.
- Outbound rows that carry an outside caller's number within 15s of that caller's inbound call are
  call-forwarding legs, not real outbound calls, so they're removed.
- Outgoing lines are named by site, e.g. "Chester · 01244 313900".

Grandstream-specific clean-up:
- Internal (extension-to-extension) calls are ignored.
- When a colleague transfers an answered call, the UCM starts a new session keyed on their extension; those are
  merged into the original call rather than counted as new inbound calls.
- A call the UCM marks "answered" with 0 seconds of talk (the caller hung up as it was picked up) counts as missed.

Bicom-specific handling:
- Line and extension legs of the same call are joined even when their unique IDs differ by a few seconds.
- Divert extensions (e.g. "Mobile Divert") always log "Answered"; the app checks the matching outbound
  leg to the mobile and only counts the call as answered if that leg was actually picked up.
- Announcement / "Busy IVR" extensions are treated as messages, not people, so a caller who only heard
  the "all busy" message counts as missed.

Vela-specific handling:
- Internal calls are ignored; outbound calls are credited to the extension that made them (e.g. "Office 1000").
- A call the PBX marks ANSWERED that never left the IVR (no ring group, 0s talk) is a **menu hang-up** and counts
  as missed (unless shorter than the "ignore hang-ups" setting); it gets its own observation and recommendation.

To add another provider later, write a `load_<provider>()` in `cdr_engine.py` that returns the same columns
as `load_trusip()`, then add it to `PROVIDERS` and `CAPS`.

## Bicom input format

Built around the Bicom export with these columns (no header, tab-separated, UTF-16):
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
| `users.py` | Per-person (user) assessment metrics and observations |
| `ui.py` | Shared look and results dashboard (also used by customer portals) |
| `ucm_client.py` | Grandstream UCM API client (used by customer portals) |
| `report_pdf.py` | 2-page A4 PDF report - "Portal" (dark, matches the web app) or "Print-friendly" (light) |
| `fonts/` | Inter font files used by the PDF (SIL Open Font Licence - see `fonts/LICENSE-Inter.txt`) |
| `.streamlit/config.toml` | Dark theme |
