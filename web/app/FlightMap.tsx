"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type * as Leaflet from "leaflet";
import "leaflet/dist/leaflet.css";
import "./flight-map.css";
import { orderedStops, maintenanceLeads } from './stopReview';

type Watch = { id: number; registration: string };
type Stop = { number: number; dataset_day_id: number; address: string; visit_sequence: number; airport_ident: string; airport_name: string; latitude_deg: number; longitude_deg: number; first_evidence_at: string; last_evidence_at: string; arrived_at: string | null; departed_at: string | null; ground_time_seconds: number; evidence_span_seconds: number; confidence: string; ground_observation_count: number; proximity_observation_count: number; closest_distance_nm: number; arrival_evidence: string | null; departure_evidence: string | null; open_at_start: boolean; open_at_end: boolean; quality_flags: string[] };
type Approval = { id: number; approval_number: string; approval_status: string; valid_from: string | null; valid_to: string | null; source_url: string | null; last_verified_at: string | null; linked_site_ids: number[] };
type Match = "SITE_MATCH" | "COMPANY_MATCH" | "POSSIBLE_FAMILY" | "STALE_MAPPING" | "APPROVAL_NOT_CURRENT" | "NO_RECORDED_MATCH";
type Capability = Approval & { capability_id: number; mappings?: { variant_scope: string; notes: string; stale: boolean }[]; company_site_id: number | null; capability_kind: string; aircraft_type_code: string | null; manufacturer: string | null; model: string | null; limitation: string | null; rating_code: string | null; is_base_maintenance: boolean; is_line_maintenance: boolean; match: Match };
type Site = { id: number; company_name: string; name: string; airport_ident: string | null; latitude_deg: number | null; longitude_deg: number | null; location_precision: string; capabilities: Capability[]; approvals: Approval[]; match: Match };
type MapData = { watch: Watch; from: string; to: string; latest_processed: string | null; type_code: string | null; type_codes: string[]; addresses: string[]; processed_days: { utc_date: string; derivation_version: string | null }[]; expected_days: number; stops: Stop[]; sites: Site[]; stops_truncated: boolean; sites_truncated: boolean; capabilities_truncated: boolean; approval_as_of: string };
const labels: Record<Match, string> = { POSSIBLE_FAMILY: "Possible family / restricted variant match", STALE_MAPPING: "Mapping needs re-review", SITE_MATCH: "Site-specific type match", COMPANY_MATCH: "Company-wide type match only", APPROVAL_NOT_CURRENT: "Type recorded; approval not current", NO_RECORDED_MATCH: "No recorded type match" };
const colors: Record<Match, string> = { POSSIBLE_FAMILY: "#d97706", STALE_MAPPING: "#c24154", SITE_MATCH: "#059669", COMPANY_MATCH: "#d97706", APPROVAL_NOT_CURRENT: "#c24154", NO_RECORDED_MATCH: "#64748b" };
const stopId = (s: Stop) => `${s.dataset_day_id}:${s.address}:${s.visit_sequence}`;
const utc = (s: string) => new Date(s).toISOString().replace("T", " ").slice(0, 16) + " UTC";
const duration = (seconds: number) => seconds < 60 ? `${Math.round(seconds)} sec` : seconds < 3600 ? `${Math.round(seconds / 60)} min` : `${(seconds / 3600).toFixed(1)} hr`;
const safeUrl = (url: string | null) => url && /^https?:\/\//i.test(url) ? url : undefined;
async function get<T>(path: string, signal: AbortSignal): Promise<T> {
  const r = await fetch(`/api/maintenance/${path}`, { signal });
  if (!r.ok) {
    const detail = await r.json().catch(() => ({}));
    throw new Error(detail.error || `Map request failed (${r.status}); try a shorter date range.`);
  }
  return r.json();
}

export function FlightMap({ onReviewCapability }: { onReviewCapability?: (id: number) => void }) {
  const [watches, setWatches] = useState<Watch[]>([]);
  const [watchId, setWatchId] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [request, setRequest] = useState({ watch: "", from: "", to: "", revision: 0 });
  const [data, setData] = useState<MapData | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [key, setKey] = useState<string | null>(null);
  const [tileError, setTileError] = useState(false);
  const [selectedStop, setSelectedStop] = useState("");
  const [timelineOrder, setTimelineOrder] = useState('TIME');
  const [atStopOnly, setAtStopOnly] = useState(true);
  const [fitRevision, setFitRevision] = useState(0);
  const [selectedSite, setSelectedSite] = useState<Site | null>(null);
  const [matchesOnly, setMatchesOnly] = useState(false);
  const [showBases, setShowBases] = useState(true);
  const [search, setSearch] = useState("");
  const element = useRef<HTMLDivElement>(null);
  const mapRef = useRef<Leaflet.Map | null>(null);
  const leaflet = useRef<typeof Leaflet | null>(null);
  const [ready, setReady] = useState(false);

  const load = useCallback((watch: string, start: string, end: string) => {
    setBusy(Boolean(watch)); setError(""); setData(null); setSelectedStop("");
    setSelectedSite(null);
    setRequest(previous => ({ watch, from: start, to: end, revision: previous.revision + 1 }));
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([get<Watch[]>("watches", controller.signal), get<{ carto_key: string }>("map-config", controller.signal)])
      .then(([items, config]) => {
        setWatches(items); setKey(config.carto_key);
        if (items.length) { setWatchId(String(items[0].id)); load(String(items[0].id), "", ""); }
      }).catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, [load]);

  useEffect(() => {
    if (!request.watch) return;
    const controller = new AbortController();
    const params = new URLSearchParams();
    if (request.from) params.set("from", request.from);
    if (request.to) params.set("to", request.to);
    get<MapData>(`watches/${request.watch}/map?${params}`, controller.signal)
      .then(result => { setData(result); setFrom(result.from); setTo(result.to); })
      .catch(e => { if (!controller.signal.aborted) setError(e.message); })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [request]);

  useEffect(() => {
    let disposed = false;
    let instance: Leaflet.Map | null = null;
    if (key === null) return;
    import("leaflet").then(L => {
      if (disposed || !element.current) return;
      leaflet.current = L;
      instance = L.map(element.current, { preferCanvas: true, zoomAnimation: false }).setView([53, 1], 5);
      mapRef.current = instance;
      if (key) L.tileLayer(`https://basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png?key=${encodeURIComponent(key)}`, {
        maxZoom: 20,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
      }).on("tileerror", () => { if (!disposed) setTileError(true); }).addTo(instance);
      setReady(true);
    }).catch(e => { if (!disposed) setError(String(e)); });
    return () => { disposed = true; instance?.remove(); mapRef.current = null; };
  }, [key]);

  const stop = data?.stops.find(s => stopId(s) === selectedStop);
  const visibleSites = (data?.sites || []).filter(s =>
    (!matchesOnly || ["SITE_MATCH", "COMPANY_MATCH", "POSSIBLE_FAMILY"].includes(s.match)) &&
    (!atStopOnly || !stop || s.airport_ident === stop.airport_ident) &&
    `${s.company_name} ${s.name} ${s.airport_ident || ""}`.toLowerCase().includes(search.toLowerCase()));

  useEffect(() => {
    const L = leaflet.current, map = mapRef.current;
    if (!L || !map || !ready || !data) return;
    const layer = L.featureGroup().addTo(map);
    // Connect consecutive records only within the same address, never across aircraft.
    const previousByAddress = new Map<string, Stop>();
    const byAirport = new Map<string, Stop[]>();
    for (const s of data.stops) {
      const previous = previousByAddress.get(s.address);
      if (previous && previous.airport_ident !== s.airport_ident &&
          Math.abs(previous.longitude_deg - s.longitude_deg) <= 180) {
        const label = document.createElement("span");
        label.textContent = `Stop ${previous.number} → ${s.number}: sequence of sightings only, not a flown route or proof of continuous coverage.`;
        L.polyline([[previous.latitude_deg, previous.longitude_deg], [s.latitude_deg, s.longitude_deg]],
          { color: "#64748b", dashArray: "5 9", weight: 2 }).bindPopup(label).addTo(layer);
      }
      previousByAddress.set(s.address, s);
      byAirport.set(s.airport_ident, [...(byAirport.get(s.airport_ident) || []), s]);
    }
    // Group repeated visits at the same airport so later stops do not hide earlier ones.
    for (const group of byAirport.values()) {
      const s = group.find(s => stopId(s) === selectedStop) || group[0];
      const active = group.some(s => stopId(s) === selectedStop);
      const ground = Math.max(...group.map(s => s.ground_time_seconds));
      const radius = 12 + Math.min(12, Math.sqrt(ground / 3600) * 4);
      const label = document.createElement("span");
      label.textContent = group.slice(0, 3).map(s => s.number).join(",") + (group.length > 3 ? "+" : "");
      label.title = `${s.airport_ident}: stops ${group.map(s => s.number).join(", ")}. Select individual visits in the timeline.`;
      L.circleMarker([s.latitude_deg, s.longitude_deg],
        { radius, color: active ? "#2563eb" : "#0f172a", fillColor: active ? "#dbeafe" : "#fff", fillOpacity: 0.95, weight: active ? 3 : 2 })
        .bindTooltip(label, { permanent: true, direction: "center", className: "map-stop-number" })
        .on("click", () => { setSelectedStop(stopId(s)); setSelectedSite(null); }).addTo(layer);
    }
    return () => { layer.remove(); };
  }, [data, ready, selectedStop]);

  useEffect(() => {
    const L = leaflet.current, map = mapRef.current;
    if (!L || !map || !ready || !data) return;
    const coords = (stop ? [stop] : data.stops).map(s => [s.latitude_deg, s.longitude_deg] as Leaflet.LatLngTuple);
    if (coords.length) {
      map.stop();
      map.fitBounds(L.latLngBounds(coords), { padding: [40, 40], maxZoom: stop ? 12 : 10, animate: false });
    }
  }, [data, ready, stop, fitRevision]);

  useEffect(() => {
    const L = leaflet.current, map = mapRef.current;
    if (!L || !map || !ready || !data || !showBases) return;
    const layer = L.layerGroup().addTo(map);
    for (const site of visibleSites) {
      if (site.latitude_deg == null || site.longitude_deg == null) continue;
      const label = document.createElement("span"); label.textContent = `${site.company_name} · ${site.name} · ${labels[site.match]}`;
      L.circleMarker([site.latitude_deg, site.longitude_deg], { radius: site.match === "SITE_MATCH" ? 8 : 6, color: colors[site.match], fillColor: colors[site.match], fillOpacity: 0.8, weight: 2 })
        .bindTooltip(label).on("click", () => setSelectedSite(site)).addTo(layer);
    }
    return () => { layer.remove(); };
  }, [data, ready, showBases, visibleSites]);

  return <main className="flight-map-page">
    <header><p className="eyebrow">Maintenance Pulse · Stop history</p><h1>Where has this aircraft been stopping?</h1><p>Existing airport visits, observed ground time and Part-145 capabilities. No flight-path recording or replay.</p></header>
    <form className="map-controls" onSubmit={e => { e.preventDefault(); load(watchId, from, to); }}>
      <label>Watched aircraft<select value={watchId} onChange={e => { setWatchId(e.target.value); load(e.target.value, from, to); }}><option value="">Select a tail</option>{watches.map(w => <option key={w.id} value={w.id}>{w.registration}</option>)}</select></label>
      <label>From (UTC)<input type="date" value={from} onChange={e => setFrom(e.target.value)} /></label>
      <label>To (UTC)<input type="date" value={to} onChange={e => setTo(e.target.value)} /></label>
      <button className="button-primary" disabled={busy || !watchId}>{busy ? "Loading…" : "Load dates"}</button>
      <span>Up to 31 days · defaults to latest processed week</span>
    </form>
    {error && <p className="map-warning" role="alert">{error}</p>}
    {!watches.length && !error && <p>Add aircraft in Maintenance Pulse to start exploring their existing stop history.</p>}
    {key === "" && <p className="map-warning">Basemap not configured. Set HELIGENT_CARTO_BASEMAP_KEY on the VPS to enable CARTO tiles. Stop and base layers still work.</p>}
    {tileError && <p className="map-warning" role="alert">CARTO tiles could not load. Check the basemap key, its permitted domain, and network access. Overlays remain available.</p>}
    {data && <>
      <div className="map-stats"><strong>{data.watch.registration} · {data.type_code || (data.type_codes.length ? "Conflicting types" : "Type unknown")}</strong><span>{data.processed_days.length}/{data.expected_days} dates processed</span><span>{data.stops.length} stop records · {new Set(data.stops.map(s => s.airport_ident)).size} airports</span><span>{duration(data.stops.reduce((sum, s) => sum + s.ground_time_seconds, 0))} observed ground time in displayed records</span></div>
      <p className="map-note">Loaded {data.from} to {data.to} UTC. Addresses: {data.addresses.join(", ") || "none observed"}. Approval matching uses records current as of {data.approval_as_of}, not historical approval at visit time.</p>
      {data.addresses.length > 1 && <p className="map-warning">Multiple addresses recorded for this registration. Histories are connected separately; check identity before combining them.</p>}
      {!data.type_code && <p className="map-warning">{data.type_codes.length ? `Conflicting recorded types: ${data.type_codes.join(", ")}.` : "No recorded aircraft type in this date range."} Type matching is disabled.</p>}
      {data.processed_days.length < data.expected_days && <p className="map-warning">Some dates are not processed. Missing visits do not establish inactivity; processed dates also have receiver coverage gaps.</p>}
      {data.stops_truncated && <p className="map-warning">Showing the latest 500 stop records, numbered chronologically within this displayed subset. Narrow the dates to inspect omitted history.</p>}
      {(data.sites_truncated || data.capabilities_truncated) && <p className="map-warning">Base/capability catalogue limit reached. Matches may be incomplete.</p>}
      {!data.stops.length && <p className="map-warning">No airport-visit records for this tail in the selected dates. Check processing coverage or try another period. This does not mean it made no stops.</p>}
    </>}
    <div className="flight-map-layout">
      <section className="map-main">
        <div className="map-legend"><span>① Stop order (UTC)</span><span>○ Larger circle = more observed ground time</span><span>┄ Sighting sequence, not a flight path</span>{Object.entries(labels).map(([k, label]) => <span key={k} style={{ color: colors[k as Match] }}>● {label}</span>)}</div>
        <div ref={element} className="flight-map-canvas" aria-label="Historical aircraft stops and Part-145 bases map" />
        <section className="map-stops">
          <div className="map-stop-heading"><h2>Stop timeline (UTC)</h2><label>Sort stops<select value={timelineOrder} onChange={e=>setTimelineOrder(e.target.value)}><option value="TIME">Chronological</option><option value="NEWEST">Newest first</option><option value="GROUND">Most observed ground time</option></select></label><button type="button" onClick={() => { setSelectedStop(""); setSelectedSite(null); setFitRevision(n => n + 1); }}>Show all stops</button></div>
          <p className="map-note">Sorting changes only this list; stop numbers and map connections stay chronological. Maintenance badges refer to airport-linked sites in the catalogue, not confirmed hangar visits.</p>
          <p className="map-note">A record is one daily airport-visit episode, not necessarily a separate landing. Repeated days at the same airport are kept separate; no stay is inferred across missing observations. Shared markers list multiple stop numbers.</p>
          {stop && <article className="map-stop-detail" aria-label="Selected stop evidence">
            <h3>Stop {stop.number} · {stop.airport_ident} · {stop.airport_name}</h3>
            <dl><dt>First evidence</dt><dd>{utc(stop.first_evidence_at)}</dd><dt>Last evidence</dt><dd>{utc(stop.last_evidence_at)}</dd>
              <dt>Observed ground time</dt><dd>{duration(stop.ground_time_seconds)} · {stop.ground_observation_count} ground observations</dd>
              <dt>Span between sightings</dt><dd>{duration(stop.evidence_span_seconds)} — not confirmed continuous time on site</dd>
              <dt>Arrival estimate</dt><dd>{stop.arrived_at ? utc(stop.arrived_at) : "Unknown / not bounded"} · {stop.arrival_evidence || "No arrival evidence"}</dd>
              <dt>Departure estimate</dt><dd>{stop.departed_at ? utc(stop.departed_at) : "Unknown / not bounded"} · {stop.departure_evidence || "No departure evidence"}</dd>
              <dt>Confidence / identity</dt><dd>{stop.confidence} · {stop.address}</dd>
              <dt>Location evidence</dt><dd>{stop.proximity_observation_count} proximity observations · closest {Number(stop.closest_distance_nm).toFixed(2)} NM from airport reference</dd></dl>
            {(stop.open_at_start || stop.open_at_end) && <p className="map-warning">This visit is open at {stop.open_at_start && stop.open_at_end ? "both UTC window boundaries" : stop.open_at_start ? "the start of its UTC window" : "the end of its UTC window"}. Arrival/departure may be outside the observed period.</p>}
            {stop.ground_observation_count === 0 && <p className="map-warning">Proximity-only evidence: no ground observations. A stop or landing is not confirmed.</p>}
            <p className="map-note">Quality flags: {stop.quality_flags.join(", ") || "none recorded"}. Airport association does not prove entry into an individual base or maintenance.</p>
          </article>}
          <div className="map-stop-list">{orderedStops(data?.stops || [],timelineOrder).map(s => <button type="button" key={stopId(s)} aria-pressed={stopId(s) === selectedStop} onClick={() => { setSelectedStop(stopId(s)); setSelectedSite(null); }}>
            <strong>{s.number}. {s.airport_ident} · {s.airport_name}</strong><span>{utc(s.first_evidence_at)} → {utc(s.last_evidence_at)}</span>
            <small>{duration(s.ground_time_seconds)} observed ground · {duration(s.evidence_span_seconds)} evidence span · {s.confidence}{s.ground_observation_count === 0 ? " · proximity only" : ""}</small>
            <span className="stop-maintenance-badges">{maintenanceLeads(data?.sites || [],s.airport_ident).length ? maintenanceLeads(data?.sites || [],s.airport_ident).map(base=><span className="stop-maintenance-badge" key={base.id} style={{borderColor:colors[base.match],color:colors[base.match]}}>{base.company_name} · {base.name}<br/>{data?.type_code ? labels[base.match] : 'Aircraft type unknown — capability not evaluated'}</span>) : <span className="map-note">No maintenance sites linked in the catalogue</span>}</span>
          </button>)}</div>
        </section>
      </section>
      <aside className="map-sidebar">
        <h2>Part-145 bases</h2><label><input type="checkbox" checked={showBases} onChange={e => setShowBases(e.target.checked)} /> Show bases on map</label><label><input type="checkbox" checked={matchesOnly} onChange={e => setMatchesOnly(e.target.checked)} /> Type matches only (including possible families)</label>
        <label><input type="checkbox" checked={atStopOnly} disabled={!stop} onChange={e => setAtStopOnly(e.target.checked)} /> At selected stop’s airport only</label>
        {stop && <p className="map-note">Selected stop {stop.number}: {stop.airport_ident}. Airport-linked bases are leads, not confirmed facility visits.</p>}
        <input aria-label="Find a base" placeholder="Company, base or airport…" value={search} onChange={e => setSearch(e.target.value)} />
        <p className="map-note">{visibleSites.length} bases · {visibleSites.filter(s => s.latitude_deg == null || s.longitude_deg == null).length} without coordinates. Shared airport markers can overlap; select an individual base below.</p>
        <div className="map-base-list">{visibleSites.slice(0, 100).map(s => <button type="button" key={s.id} onClick={() => { setSelectedSite(s); if (s.latitude_deg != null && s.longitude_deg != null) mapRef.current?.setView([s.latitude_deg, s.longitude_deg], 13); }}><strong>{s.company_name}</strong><span>{s.name} · {s.airport_ident || "Airport not linked"}</span><small style={{ color: colors[s.match] }}>{labels[s.match]}</small></button>)}{visibleSites.length > 100 && <p>Showing first 100 in this list. Search to narrow it; all located results remain on the map.</p>}</div>
        {selectedSite && <section className="map-base-detail"><h3>{selectedSite.company_name}</h3><p>{selectedSite.name} · {selectedSite.airport_ident || "No airport link"}</p><strong style={{ color: colors[selectedSite.match] }}>{labels[selectedSite.match]}</strong><p>{selectedSite.location_precision === "AIRPORT_CENTROID" ? "Marker is the airport centre, not the hangar." : selectedSite.location_precision === "UNLOCATED" ? "No mapped coordinates." : "Imported site coordinate; accuracy has not been independently verified."}</p>
          <p className="map-note">Exact recorded codes or reviewed mappings; broad families and unverified variants remain possible matches. No recorded match does not mean incapable. A type match does not confirm the required check, variant, tooling or slot availability.</p>
          <h4>Approval records</h4>{selectedSite.approvals.map(a => <p key={a.id}>{a.approval_number} · {a.approval_status}<br />{a.linked_site_ids.includes(selectedSite.id) ? "Explicit site–approval link" : "Company approval; site linkage not recorded"}<br />Validity: {a.valid_from || "unknown"} to {a.valid_to || "not recorded"}<br />Last verified: {a.last_verified_at ? utc(a.last_verified_at) : "unknown"}{safeUrl(a.source_url) && <><br /><a href={safeUrl(a.source_url)} target="_blank" rel="noreferrer">Source record ↗</a></>}</p>)}
          <h4>Recorded capabilities ({selectedSite.capabilities.length})</h4>{!selectedSite.capabilities.length && <p>No site-specific or company-wide capability records available.</p>}{selectedSite.capabilities.map((c, i) => <article key={i}><strong>{c.aircraft_type_code || c.model || c.capability_kind}</strong><span>{[c.manufacturer, c.model, c.rating_code].filter(Boolean).join(" · ")}</span><small style={{ color: colors[c.match] }}>{labels[c.match]}</small><p>{c.company_site_id == null ? "Company-wide scope — verify this site" : "Site-specific record"} · {c.approval_number} · {c.approval_status}</p><p>{c.is_base_maintenance ? "Base maintenance" : ""}{c.is_line_maintenance ? " · Line maintenance" : ""}</p><p>{c.limitation || "No limitations supplied; consult the approval scope."}</p>{c.mappings?.map((m, j) => <p key={j}>{m.stale ? "Source changed; re-review required. " : ""}{m.variant_scope && `Restricted variants: ${m.variant_scope}. `}{m.notes}</p>)}{c.capability_kind === "AIRCRAFT" && onReviewCapability && <button type="button" onClick={() => onReviewCapability(c.capability_id)}>Review type mappings</button>}</article>)}
        </section>}
      </aside>
    </div>
    <p className="map-note">This map reads existing airport-visit summaries. No intermediate coordinates are stored, no stops are inferred across reception gaps, and no map action starts ingestion or changes reviews. Base records may be incomplete or stale.</p>
  </main>;
}
