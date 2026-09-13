# European helicopter rest and maintenance-signal investigation

Read-only production audit, 13 September 2026. Production code revision: `b31c3d7`.

## Scope and method

Production contains 40 consecutive episode-capable dates, 1 August–9 September 2026, using `flight-visits-v1`. The 10 September job failed during discovery because the Pi archive did not have a verified copy. Eight older processed dates have legacy daily summaries and were excluded.

Screened 39,595 aircraft-days for 3,368 database-classified rotorcraft addresses with at least one European airport visit. The export includes the selected addresses' worldwide visits, rather than restricting their subsequent activity to Europe. This is a screening population, not a count of civil European helicopters or a complete register.

For each address, compared successive observed days. A possible rest window requires an open final airport visit, an open initial visit at the next observed day, the same European airport at both ends, and at most 60 seconds between each airport evidence endpoint and the aircraft's daily trace endpoint. Windows of at least six hours were retained. This found 17,810 possible same-airport quiet intervals. These are gaps between observations; their duration is not measured continuous ground time. UTC date crossings are used here, not a formal local-night definition.

The shortlisted registrations have stable registration/type values in the audited period. Checked unfiltered production aircraft-day records for the long-window examples so a changed or missing daily type classification would not conceal intervening records. Date completeness is distinct from reception coverage.

## Shortlist

| Registration | Address | Stored type | Location | Useful signal |
|---|---|---|---|---|
| G-PJCD | 407fb9 | A139 / AW139 | Norwich EGSH | 38 observed days; 32 same-airport quiet windows; median 13.8 hours. Ground evidence in both boundary visits for 31 windows. Good routine overnight reference. |
| D-HXFG | 3e0fe4 | EC45 / EC145 family | Nuremberg EDDN | 34 observed days; 33 same-airport quiet windows; median 15.0 hours. Ground evidence on both sides of 31 windows. |
| LN-OQC | 4783f0 | S92 / S-92 | Bergen ENBR | 33 observed days; 29 same-airport quiet windows; median 14.9 hours. Ground evidence on both sides of 28 windows. |
| G-PJCS | 40809b | A139 / AW139 | Norwich EGSH | 31 quiet windows, all with ground evidence on both sides; a 4.84-day interval on 12–17 August stands out against its usual 13.7-hour median. |
| LN-OLA | 47804b | A139 / AW139 | Bergen ENBR | 19.77-day interval on 8–28 August, with ground evidence on both sides and no intervening aircraft-day records. |
| G-SNSI | 406942 | A139 / AW139 | Norwich EGSH | Already watched. 19.95-day interval on 20 August–9 September, with ground evidence on both sides and no intervening aircraft-day records. |
| G-LZZI | 407f97 | EC45 / EC145 family | Oxford EGTK | Already watched. Repeated multi-day Oxford gaps; existing MRO view already returns eight candidates, including 8.57 and 6.69 days. |

The EC45 entries retain the database's EC145-family description; this investigation does not independently resolve their precise variant.

### Exact long-window evidence (UTC, 2026)

| Aircraft | Last observation | Next observation | Quiet hours | Ground observations in boundary visits | Observed ground seconds in boundary visits |
|---|---|---|---:|---:|---:|
| G-SNSI | 20 Aug 12:36:32 | 9 Sep 11:27:21 | 478.85 | 43 / 21 | 40 / 32 |
| LN-OLA | 8 Aug 22:51:51 | 28 Aug 17:18:27 | 474.44 | 82 / 61 | 134 / 120 |
| G-PJCS | 12 Aug 11:49:43 | 17 Aug 08:04:08 | 116.24 | 116 / 102 | 626 / 846 |
| G-LZZI | 12 Aug 19:13:08 | 21 Aug 08:47:48 | 205.58 | 0 / 0 | 0 / 0 |

Other classified helicopters were recorded at the relevant airport on every fully intervening date: G-SNSI 19/19 dates (3–9 other tails daily), LN-OLA 19/19 (2–11), G-PJCS 4/4 (5–7), G-LZZI 8/8 (2–7). This supports continued reception around those airports; it cannot establish the missing aircraft's continuous presence.

## Why the ground metric is weak

1. **It measures explicit ground-state samples.** `summarize.py:929` only recognizes literal `"ground"` in the source altitude field. This matches the upstream [readsb trace format](https://github.com/wiedehopf/readsb/blob/dev/README-json.md#trace-jsons). Low speed or numeric altitude alone is not a ground-state report. Helicopter hover makes speed-only ground classification unsuitable.
2. **The source itself varies substantially by aircraft and location.** Across the 40 dates, D-HNWV has 7 explicit ground observations among 137,908 positions (0.005%); G-PJCD has 20,921 among 65,321 (32.0%). Both have only `adsb_icao` in their daily source lists. This contrast is not attributable to MLAT in these two examples.
3. **Checked original traces, not just summaries.** Public source traces for 9 September contain 1,899 positions and zero ground labels for D-HNWV, versus 3,250 positions and 1,250 ground labels for G-PJCD. These were fetched from the [D-HNWV trace](https://globe.adsb.lol/globe_history/2026/09/09/traces/45/trace_full_3df745.json) and [G-PJCD trace](https://globe.adsb.lol/globe_history/2026/09/09/traces/b9/trace_full_407fb9.json). The data does not identify why a particular aircraft lacks ground reports.
4. **Parked time can be silent.** An inactive transmitter contributes no positions; a ground-level transmitter may also have poor reception. ADS-B reception depends on line of sight; [FlightAware's receiver explanation](https://www.flightaware.com/adsb/) describes this constraint. [MLAT](https://www.flightaware.com/adsb/mlat/) adds a requirement for multiple receiving stations, which can further limit ground coverage for MLAT-dependent aircraft.
5. **The duration calculation discards gaps.** Airport ground time sums only intervals no more than 300 seconds apart, with explicit ground state throughout. It does not extend silence to the next departure. The displayed episodes are also split by UTC day.
6. **Airport proximity can create a visit without a landing.** Internal contacts permit low/slow points up to 500 feet above airport reference elevation and 100 knots, with broader first/last-point thresholds. This is useful endpoint evidence, but the word “stop” makes it sound stronger than it is.

Upstream traces also contain stale-position and suggested new-leg flags. The current `_Point` representation discards those flags. They are useful candidates for a subsequent parser improvement, with validation against known events; a source leg flag is itself only a heuristic.

## Why maintenance-scale signals are hidden

The map reads daily `aircraft_airport_visit` rows; it does not display the cross-day intervals from `nl_mro_stay_candidate`.

That view requires an active MRO company with an active airport-linked site. Production currently has nine eligible European airports: EDAQ, EDKB, EDMO, EDMS, EDSB, EDWI, EGTK, LFBD and LFSB. Norwich, Bergen, Nuremberg and Den Helder are absent from this gate. This is a catalogue linkage limitation, not evidence that those airports lack maintenance providers.

G-LZZI already has eight candidates in the existing view, all graded MEDIUM, including 8.57 days at Oxford on 12–21 August and 6.69 days on 23–30 August. G-SNSI and LN-OLA cannot enter this model at Norwich/Bergen under the current catalogue gate despite their long same-airport gaps.

## Suggested product changes, in priority order

1. **A continuous activity-and-stay timeline.** Show flight episodes, explicit ground evidence, possible same-airport quiet windows, and unresolved time. Join across midnight. Permit elapsed days/hours as the headline measure while retaining observed seconds as evidence. Distinguish tail silence from unprocessed source dates and show peer-airport activity for the gap.
2. **An overnight-location calendar and likely-base ranking.** Use the last location and next observed start to assign a likely night location, with local time zones and shifts that span midnight. Rank airports by nights associated with them, not ground seconds. Preserve “unknown” and “changed location” states. Extend to repeat endpoint clusters for off-airport bases, since the airport catalogue will miss some helicopter locations.
3. **A list of unusual stays.** Generate rest windows for every location first. Add MRO/company/type-capability information as enrichment. Rank longer-than-usual stays, away-from-usual-base stays, and changes in flying cadence relative to each aircraft's baseline. An ordinary 60-hour weekend should be visible without automatically becoming a maintenance lead.
4. **Overlay your known maintenance events.** Put confirmed event dates/sites on the same timeline and compare event periods with the observed patterns, including missed events. Retain separate assessments of location evidence and maintenance relevance. This is the useful calibration loop for your industry knowledge.

The first version of cross-day rest windows, timeline and airport-based overnight ranking can use existing compact database records. More detailed stationary/off-airport inference and preservation of source leg flags would need a parser update and selected raw reprocessing.

The accompanying interactive timeline uses seven shortlisted aircraft and 158 possible rest windows. Browser checks covered aircraft/interval selection and desktop/mobile layouts. No production data or settings were changed by this investigation.
