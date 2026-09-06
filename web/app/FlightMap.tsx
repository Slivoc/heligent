"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type * as Leaflet from "leaflet";
import "leaflet/dist/leaflet.css";
import "./flight-map.css";

type Fix = [number, number, number, number | null]; // epoch seconds, lat, lon, altitude ft
type Track = { segments: Fix[][]; retained_count: number; input_count: number; truncated: boolean };
type Watch = { id: number; registration: string };
type Flight = { dataset_day_id: number; address: string; segment_sequence: number; takeoff_at: string; landing_at: string; origin_airport_ident: string | null; destination_airport_ident: string | null; origin_lat: number | null; origin_lon: number | null; destination_lat: number | null; destination_lon: number | null; track: Track | null; track_omitted: boolean; confidence: string; quality_flags: string[] };
type Visit = { airport_ident: string; airport_name: string; latitude_deg: number; longitude_deg: number; first_evidence_at: string; last_evidence_at: string; confidence: string; ground_observation_count: number };
type Approval = { id: number; approval_number: string; approval_status: string; valid_from: string | null; valid_to: string | null; source_url: string | null; last_verified_at: string | null; linked_site_ids: number[] };
type Match = "SITE_MATCH" | "COMPANY_MATCH" | "APPROVAL_NOT_CURRENT" | "NO_RECORDED_MATCH";
type Capability = Approval & { company_site_id: number | null; capability_kind: string; aircraft_type_code: string | null; manufacturer: string | null; model: string | null; limitation: string | null; rating_code: string | null; is_base_maintenance: boolean; is_line_maintenance: boolean; match: Match };
type Site = { id: number; company_name: string; name: string; airport_ident: string | null; latitude_deg: number | null; longitude_deg: number | null; location_precision: string; capabilities: Capability[]; approvals: Approval[]; match: Match };
type MapData = { watch: Watch; from: string; to: string; latest_processed: string | null; type_code: string | null; type_codes: string[]; addresses: string[]; processed_days: { utc_date: string; derivation_version: string | null }[]; expected_days: number; flights: Flight[]; visits: Visit[]; sites: Site[]; flights_truncated: boolean; visits_truncated: boolean; sites_truncated: boolean; capabilities_truncated: boolean; approval_as_of: string };
const labels: Record<Match, string> = { SITE_MATCH: "Site-specific type match", COMPANY_MATCH: "Company-wide type match only", APPROVAL_NOT_CURRENT: "Type recorded; approval not current", NO_RECORDED_MATCH: "No recorded type match" };
const colors: Record<Match, string> = { SITE_MATCH: "#059669", COMPANY_MATCH: "#d97706", APPROVAL_NOT_CURRENT: "#c24154", NO_RECORDED_MATCH: "#64748b" };
const flightId = (f: Flight) => `${f.dataset_day_id}:${f.address}:${f.segment_sequence}`;
const utc = (s: string) => new Date(s).toISOString().replace("T", " ").slice(0, 16) + " UTC";
const route = (f: Flight) => `${f.origin_airport_ident || "Unknown origin"} → ${f.destination_airport_ident || "Unknown destination"}`;
const safeUrl = (url: string | null) => url && /^https?:\/\//i.test(url) ? url : undefined;
async function get<T>(path: string, signal: AbortSignal): Promise<T> {
  const r = await fetch(`/api/maintenance/${path}`, { signal });
  if (!r.ok) {
    const detail = await r.json().catch(() => ({}));
    throw new Error(detail.error || `Map request failed (${r.status}); try a shorter date range.`);
  }
  return r.json();
}

export function FlightMap() {
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
  const [selectedFlight, setSelectedFlight] = useState("");
  const [selectedSite, setSelectedSite] = useState<Site | null>(null);
  const [matchesOnly, setMatchesOnly] = useState(false);
  const [showBases, setShowBases] = useState(true);
  const [search, setSearch] = useState("");
  const [progress, setProgress] = useState(100);
  const [playing, setPlaying] = useState(false);
  const element = useRef<HTMLDivElement>(null);
  const mapRef = useRef<Leaflet.Map | null>(null);
  const leaflet = useRef<typeof Leaflet | null>(null);
  const [ready, setReady] = useState(false);

  const load = useCallback((watch: string, start: string, end: string) => {
    setBusy(Boolean(watch)); setError(""); setData(null); setSelectedFlight("");
    setSelectedSite(null); setPlaying(false); setProgress(100);
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

  const flight = data?.flights.find(f => flightId(f) === selectedFlight);
  const fixes = flight?.track?.segments.flat() || [];
  const startTime = fixes[0]?.[0] || 0;
  const endTime = fixes.at(-1)?.[0] || 0;
  const cursorTime = startTime + (endTime - startTime) * progress / 100;
  const cursorFix = fixes.findLast(p => p[0] <= cursorTime);
  useEffect(() => {
    if (!playing) return;
    const timer = window.setTimeout(() => {
      setProgress(Math.min(100, progress + 1));
      if (progress >= 99) setPlaying(false);
    }, 100);
    return () => window.clearTimeout(timer);
  }, [playing, progress]);

  // Geometry updates do not reset pan/zoom; fit only on data/flight changes.
  useEffect(() => {
    const L = leaflet.current, map = mapRef.current;
    if (!L || !map || !ready || !data) return;
    const layer = L.featureGroup().addTo(map);
    const popup = (title: string, detail: string) => {
      const node = document.createElement("div");
      const strong = document.createElement("strong"); strong.textContent = title;
      const p = document.createElement("p"); p.textContent = detail;
      node.append(strong, p); return node;
    };
    for (const f of data.flights.filter(f => !selectedFlight || flightId(f) === selectedFlight)) {
      const description = `${utc(f.takeoff_at)} · ${f.address} · ${f.confidence} confidence. ${f.quality_flags.join(", ")}`;
      if (f.track) {
        for (const segment of f.track.segments) {
          const points = segment.filter(p => !selectedFlight || p[0] <= cursorTime);
          if (!points.length) continue;
          const coords = points.map(p => [p[1], p[2]] as Leaflet.LatLngTuple);
          const shape = coords.length > 1 ? L.polyline(coords, { color: "#2563eb", weight: 3 }) : L.circleMarker(coords[0], { radius: 3, color: "#2563eb" });
          shape.bindPopup(popup(route(f), `Sampled ADS-B track. ${description}`)).on("click", () => { setSelectedFlight(flightId(f)); setProgress(100); setPlaying(false); }).addTo(layer);
        }
      } else if (!f.track_omitted && f.origin_lat != null && f.origin_lon != null && f.destination_lat != null && f.destination_lon != null && Math.abs(f.origin_lon - f.destination_lon) <= 180) {
        L.polyline([[f.origin_lat, f.origin_lon], [f.destination_lat, f.destination_lon]], { color: "#64748b", dashArray: "6 8", weight: 2 })
          .bindPopup(popup(route(f), `Inferred airport connection only — NOT an observed flight path. ${description}`)).addTo(layer);
      }
    }
    for (const v of data.visits) {
      if (flight && (v.last_evidence_at < flight.takeoff_at || v.first_evidence_at > flight.landing_at) && ![flight.origin_airport_ident, flight.destination_airport_ident].includes(v.airport_ident)) continue;
      L.circleMarker([v.latitude_deg, v.longitude_deg], { radius: 6, color: "#0f172a", fillColor: "#fff", fillOpacity: 1, weight: 2 })
        .bindPopup(popup(`${v.airport_ident} · ${v.airport_name}`, `${utc(v.first_evidence_at)} to ${utc(v.last_evidence_at)} · ${v.confidence} confidence · ${v.ground_observation_count} ground observations. Airport association, not proof of a facility visit.`)).addTo(layer);
    }
    if (selectedFlight && cursorFix) L.circleMarker([cursorFix[1], cursorFix[2]], { radius: 8, color: "#1e3a8a", fillColor: "#60a5fa", fillOpacity: 1, weight: 3 })
      .bindPopup(popup("Last observed fix", `${utc(new Date(cursorFix[0] * 1000).toISOString())} · ${cursorFix[3] ?? "Unknown"} ft`)).addTo(layer);
    return () => { layer.remove(); };
  }, [data, ready, selectedFlight, cursorTime, cursorFix, flight]);

  useEffect(() => {
    const L = leaflet.current, map = mapRef.current;
    if (!L || !map || !ready || !data) return;
    const selected = data.flights.filter(f => !selectedFlight || flightId(f) === selectedFlight);
    const coords: Leaflet.LatLngTuple[] = [];
    for (const f of selected) {
      if (f.track) for (const segment of f.track.segments) for (const p of segment) coords.push([p[1], p[2]]);
      if (f.origin_lat != null && f.origin_lon != null) coords.push([f.origin_lat, f.origin_lon]);
      if (f.destination_lat != null && f.destination_lon != null) coords.push([f.destination_lat, f.destination_lon]);
    }
    if (!coords.length) for (const v of data.visits) coords.push([v.latitude_deg, v.longitude_deg]);
    if (coords.length) {
      map.stop(); // Cancel a base-selection pan before fitting the flight.
      map.fitBounds(L.latLngBounds(coords), { padding: [35, 35], maxZoom: 12, animate: false });
    }
  }, [data, ready, selectedFlight]);

  useEffect(() => {
    const L = leaflet.current, map = mapRef.current;
    if (!L || !map || !ready || !data || !showBases) return;
    const layer = L.layerGroup().addTo(map);
    for (const site of data.sites) {
      if (site.latitude_deg == null || site.longitude_deg == null || (matchesOnly && !["SITE_MATCH", "COMPANY_MATCH"].includes(site.match))) continue;
      if (search && !`${site.company_name} ${site.name} ${site.airport_ident || ""}`.toLowerCase().includes(search.toLowerCase())) continue;
      const label = document.createElement("span"); label.textContent = `${site.company_name} · ${site.name} · ${labels[site.match]}`;
      L.circleMarker([site.latitude_deg, site.longitude_deg], { radius: site.match === "SITE_MATCH" ? 9 : 7, color: colors[site.match], fillColor: colors[site.match], fillOpacity: 0.8, weight: 2 })
        .bindTooltip(label).on("click", () => setSelectedSite(site)).addTo(layer);
    }
    return () => { layer.remove(); };
  }, [data, ready, matchesOnly, showBases, search]);

  const visibleSites = (data?.sites || []).filter(s => (!matchesOnly || ["SITE_MATCH", "COMPANY_MATCH"].includes(s.match)) && `${s.company_name} ${s.name} ${s.airport_ident || ""}`.toLowerCase().includes(search.toLowerCase()));
  return <main className="flight-map-page">
    <header><p className="eyebrow">Maintenance Pulse · Historical evidence</p><h1>Watched aircraft & maintenance bases</h1><p>Explore observed flights and Part-145 capability records. Proximity is a lead to investigate, not proof of maintenance.</p></header>
    <form className="map-controls" onSubmit={e => { e.preventDefault(); load(watchId, from, to); }}>
      <label>Watched aircraft<select value={watchId} onChange={e => { setWatchId(e.target.value); load(e.target.value, from, to); }}><option value="">Select a tail</option>{watches.map(w => <option key={w.id} value={w.id}>{w.registration}</option>)}</select></label>
      <label>From (UTC)<input type="date" value={from} onChange={e => setFrom(e.target.value)} /></label>
      <label>To (UTC)<input type="date" value={to} onChange={e => setTo(e.target.value)} /></label>
      <button className="button-primary" disabled={busy || !watchId}>{busy ? "Loading…" : "Load dates"}</button>
      <span>Up to 31 days · defaults to latest processed week</span>
    </form>
    {error && <p className="map-warning" role="alert">{error}</p>}
    {!watches.length && !error && <p>Add aircraft in Maintenance Pulse to start exploring.</p>}
    {key === "" && <p className="map-warning">Basemap not configured. Set HELIGENT_CARTO_BASEMAP_KEY on the VPS to enable CARTO tiles. Aircraft and base layers still work.</p>}
    {tileError && <p className="map-warning" role="alert">CARTO tiles could not load. Check the basemap key, its permitted domain, and network access. Overlays remain available.</p>}
    {data && <>
      <div className="map-stats"><strong>{data.watch.registration} · {data.type_code || (data.type_codes.length ? "Conflicting types" : "Type unknown")}</strong><span>{data.processed_days.length}/{data.expected_days} dates processed</span><span>{data.flights.filter(f => f.track).length}/{data.flights.length} displayed episodes with tracks</span><span>{data.visits.length} airport visits</span><span>{data.sites.filter(s => s.match === "SITE_MATCH").length} site-specific type matches</span></div>
      <p className="map-note">Loaded {data.from} to {data.to} UTC. Addresses: {data.addresses.join(", ") || "none observed"}. Approval matching uses records current as of {data.approval_as_of}, not historical approval at flight time.</p>
      {data.addresses.length > 1 && <p className="map-warning">Multiple addresses recorded for this registration. Check identity before combining its history.</p>}
      {!data.type_code && <p className="map-warning">{data.type_codes.length ? `Conflicting recorded types: ${data.type_codes.join(", ")}.` : "No recorded aircraft type in this date range."} Type matching is disabled.</p>}
      {data.processed_days.length < data.expected_days && <p className="map-warning">Some dates are not processed. Missing tracks or visits do not establish inactivity; processed dates also have receiver coverage gaps.</p>}
      {data.flights.some(f => !f.track && !f.track_omitted) && <p className="map-note">Some episodes have no retained coordinates (older parser or tail not watched when processed). Dashed lines are inferred airport connections, not flown routes. Add the tail to the watchlist before reprocessing dates for tracks.</p>}
      {(data.flights_truncated || data.visits_truncated || data.flights.some(f => f.track_omitted)) && <p className="map-warning">Display limit reached (250 episodes, 500 visits or 30,000 track points). Narrow the dates to see omitted evidence.</p>}
      {(data.sites_truncated || data.capabilities_truncated) && <p className="map-warning">Base/capability catalogue limit reached. Matches may be incomplete.</p>}
      {!data.flights.length && <p className="map-warning">No flight episodes for this tail in the selected dates. Check processing coverage and try a different period.</p>}
    </>}
    <div className="flight-map-layout">
      <section className="map-main">
        <div className="map-legend"><span style={{ color: "#2563eb" }}>━ Sampled observed track</span><span>┄ Inferred connection</span><span>○ Airport visit</span>{Object.entries(labels).map(([k, label]) => <span key={k} style={{ color: colors[k as Match] }}>● {label}</span>)}</div>
        <div ref={element} className="flight-map-canvas" aria-label="Historical aircraft flights and Part-145 bases map" />
        <div className="map-playback">
          <label>Flight episode<select value={selectedFlight} onChange={e => { setSelectedFlight(e.target.value); setProgress(100); setPlaying(false); }}><option value="">All episodes in date range</option>{data?.flights.map(f => <option key={flightId(f)} value={flightId(f)}>{utc(f.takeoff_at)} · {route(f)} · {f.address}</option>)}</select></label>
          {flight && <><p>{route(flight)} · {flight.confidence} confidence · {flight.quality_flags.join(", ") || "No episode quality flags"}</p>{flight.track?.truncated && <p className="map-warning">This track reached its 2,048-point retention cap; its later path is omitted.</p>}{fixes.length > 0 ? <><div className="map-scrubber"><button type="button" onClick={() => { if (progress >= 100) setProgress(0); setPlaying(!playing); }}>{playing ? "Pause" : "Play"}</button><input aria-label="Flight playback position" type="range" min="0" max="100" value={progress} onChange={e => { setProgress(Number(e.target.value)); setPlaying(false); }} /></div><p>Playback: {utc(new Date(cursorTime * 1000).toISOString())}. Last observed fix: {cursorFix ? utc(new Date(cursorFix[0] * 1000).toISOString()) : "none"}. The marker holds its last known position through gaps.</p></> : <p>No retained track available for playback{flight.track_omitted ? "; narrow the dates to load it" : "; reprocess this date if needed"}.</p>}</>}
        </div>
      </section>
      <aside className="map-sidebar">
        <h2>Part-145 bases</h2><label><input type="checkbox" checked={showBases} onChange={e => setShowBases(e.target.checked)} /> Show bases on map</label><label><input type="checkbox" checked={matchesOnly} onChange={e => setMatchesOnly(e.target.checked)} /> Type matches only (site or company)</label>
        <input aria-label="Find a base" placeholder="Company, base or airport…" value={search} onChange={e => setSearch(e.target.value)} />
        <p className="map-note">{visibleSites.length} bases · {visibleSites.filter(s => s.latitude_deg == null || s.longitude_deg == null).length} without coordinates. Shared airport markers can overlap; select an individual base below.</p>
        <div className="map-base-list">{visibleSites.slice(0, 100).map(s => <button type="button" key={s.id} onClick={() => { setSelectedSite(s); if (s.latitude_deg != null && s.longitude_deg != null) mapRef.current?.setView([s.latitude_deg, s.longitude_deg], 13); }}><strong>{s.company_name}</strong><span>{s.name} · {s.airport_ident || "Airport not linked"}</span><small style={{ color: colors[s.match] }}>{labels[s.match]}</small></button>)}{visibleSites.length > 100 && <p>Showing first 100 in this list. Search to narrow it; all located results remain on the map.</p>}</div>
        {selectedSite && <section className="map-base-detail"><h3>{selectedSite.company_name}</h3><p>{selectedSite.name} · {selectedSite.airport_ident || "No airport link"}</p><strong style={{ color: colors[selectedSite.match] }}>{labels[selectedSite.match]}</strong><p>{selectedSite.location_precision === "AIRPORT_CENTROID" ? "Marker is the airport centre, not the hangar." : selectedSite.location_precision === "UNLOCATED" ? "No mapped coordinates." : "Imported site coordinate; accuracy has not been independently verified."}</p>
          <p className="map-note">Exact recorded ICAO type codes only. No recorded match does not mean incapable. A type match does not confirm the required check, variant, tooling or slot availability.</p>
          <h4>Approval records</h4>{selectedSite.approvals.map(a => <p key={a.id}>{a.approval_number} · {a.approval_status}<br />{a.linked_site_ids.includes(selectedSite.id) ? "Explicit site–approval link" : "Company approval; site linkage not recorded"}<br />Validity: {a.valid_from || "unknown"} to {a.valid_to || "not recorded"}<br />Last verified: {a.last_verified_at ? utc(a.last_verified_at) : "unknown"}{safeUrl(a.source_url) && <><br /><a href={safeUrl(a.source_url)} target="_blank" rel="noreferrer">Source record ↗</a></>}</p>)}
          <h4>Recorded capabilities ({selectedSite.capabilities.length})</h4>{!selectedSite.capabilities.length && <p>No site-specific or company-wide capability records available.</p>}{selectedSite.capabilities.map((c, i) => <article key={i}><strong>{c.aircraft_type_code || c.model || c.capability_kind}</strong><span>{[c.manufacturer, c.model, c.rating_code].filter(Boolean).join(" · ")}</span><small style={{ color: colors[c.match] }}>{labels[c.match]}</small><p>{c.company_site_id == null ? "Company-wide scope — verify this site" : "Site-specific record"} · {c.approval_number} · {c.approval_status}</p><p>{c.is_base_maintenance ? "Base maintenance" : ""}{c.is_line_maintenance ? " · Line maintenance" : ""}</p><p>{c.limitation || "No limitations supplied; consult the approval scope."}</p></article>)}
        </section>}
      </aside>
    </div>
    <p className="map-note">Tracks retain sampled airborne observations, not full-resolution raw traces or ground taxi paths. Lines between samples are visual connections; gaps are not interpolated. Base records may be incomplete or stale. No map action starts ingestion or changes reviews.</p>
  </main>;
}
