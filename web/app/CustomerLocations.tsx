"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

type Site = {
  site_id: number;
  company_id: number;
  company: string;
  is_customer: boolean;
  is_mro: boolean;
  is_operator: boolean;
  site: string;
  address_line_1?: string | null;
  address_line_2?: string | null;
  locality?: string | null;
  region?: string | null;
  postal_code?: string | null;
  country_code?: string | null;
  is_of_interest: boolean;
  interest_notes?: string | null;
  airport_ident?: string | null;
  airport_iata?: string | null;
  airport_name?: string | null;
  airport_municipality?: string | null;
  approval_numbers?: string | null;
  capability_count: number;
};

type Airport = {
  ident: string;
  iata_code?: string | null;
  name: string;
  municipality?: string | null;
  iso_country?: string | null;
};

type Capability = {
  approval_number: string;
  approval_status: string;
  capability_kind: string;
  rating_code?: string | null;
  manufacturer?: string | null;
  model?: string | null;
  aircraft_type_code?: string | null;
  capability?: string | null;
};

type Activity = {
  utc_date: string;
  site: Site;
  attribution_note: string;
  airport_metrics?: {
    airport_ident: string;
    airport_name: string;
    iata_code?: string | null;
    unique_aircraft: number;
    movement_candidates: number;
    arrival_candidates: number;
    departure_candidates: number;
    ground_observations: number;
    ground_active_hours: number;
  } | null;
  capabilities: Capability[];
  aircraft: Array<{
    address: string;
    registration?: string | null;
    operator?: string | null;
    operator_source_code?: string | null;
    type_code?: string | null;
    airport_arrival_candidates: number;
    airport_departure_candidates: number;
    activity_evidence?: string | null;
  }>;
  types: Array<{
    type_code: string;
    description?: string | null;
    unique_aircraft: number;
    arrival_candidates: number;
    departure_candidates: number;
  }>;
};

function apiBase(): string {
  return typeof window !== "undefined" && window.location.port === "3000"
    ? "http://127.0.0.1:5080"
    : "";
}

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBase()}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Requested-With": "HeligentAdmin",
      ...(options?.headers ?? {}),
    },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error ?? `Request failed (${response.status})`);
  return payload as T;
}

const numberFormatter = new Intl.NumberFormat("en-GB");

function address(site: Site): string {
  return [
    site.address_line_1,
    site.address_line_2,
    site.locality,
    site.region,
    site.postal_code,
    site.country_code,
  ].filter(Boolean).join(", ");
}

export function CustomerLocations() {
  const [query, setQuery] = useState("");
  const [trackedOnly, setTrackedOnly] = useState(false);
  const [sites, setSites] = useState<Site[]>([]);
  const [selected, setSelected] = useState<Site | null>(null);
  const [isCustomer, setIsCustomer] = useState(false);
  const [isOfInterest, setIsOfInterest] = useState(false);
  const [notes, setNotes] = useState("");
  const [airport, setAirport] = useState<Airport | null>(null);
  const [airportQuery, setAirportQuery] = useState("");
  const [airports, setAirports] = useState<Airport[]>([]);
  const [activity, setActivity] = useState<Activity | null>(null);
  const [activityDate, setActivityDate] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadSites = useCallback(async (search = "", tracked = false) => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ tracked: String(tracked) });
      if (search.trim()) params.set("q", search.trim());
      const result = await api<{ sites: Site[] }>(`/api/company-sites?${params}`);
      setSites(result.sites);
      setError(null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Unable to load company sites");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadActivity = useCallback(async (siteId: number, date?: string) => {
    try {
      const suffix = date ? `?date=${encodeURIComponent(date)}` : "";
      const result = await api<Activity>(`/api/company-sites/${siteId}/activity${suffix}`);
      setActivity(result);
      setActivityDate(result.utc_date);
      setError(null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Unable to load location activity");
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadSites("", false), 0);
    return () => window.clearTimeout(timer);
  }, [loadSites]);

  const chooseSite = (site: Site) => {
    setSelected(site);
    setIsCustomer(site.is_customer);
    setIsOfInterest(site.is_of_interest);
    setNotes(site.interest_notes ?? "");
    setAirport(site.airport_ident ? {
      ident: site.airport_ident,
      iata_code: site.airport_iata,
      name: site.airport_name ?? site.airport_ident,
      municipality: site.airport_municipality,
    } : null);
    setAirportQuery("");
    setAirports([]);
    setNotice(null);
    void loadActivity(site.site_id);
  };

  const searchSites = (event: FormEvent) => {
    event.preventDefault();
    void loadSites(query, trackedOnly);
  };

  const searchAirports = async (event: FormEvent) => {
    event.preventDefault();
    try {
      const result = await api<{ airports: Airport[] }>(
        `/api/airports/search?q=${encodeURIComponent(airportQuery)}`,
      );
      setAirports(result.airports);
      setError(null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Unable to search airports");
    }
  };

  const save = async () => {
    if (!selected) return;
    setSaving(true);
    try {
      const result = await api<{ site: Site }>(
        `/api/company-sites/${selected.site_id}/tracking`,
        {
          method: "POST",
          body: JSON.stringify({
            airport_ident: airport?.ident ?? null,
            is_customer: isCustomer,
            is_of_interest: isOfInterest,
            interest_notes: notes,
          }),
        },
      );
      setSelected(result.site);
      setNotice("Customer and location settings saved.");
      await loadSites(query, trackedOnly);
      await loadActivity(result.site.site_id, activityDate || undefined);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Unable to save the location");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="locations-surface">
      <section className="locations-hero">
        <div>
          <p className="eyebrow">Customer and base intelligence</p>
          <h1>Choose the addresses that matter.</h1>
          <p>Mark customers, watch individual approved sites, and manually connect each address to the airport whose aggregate activity you want to follow.</p>
        </div>
        <div className="locations-guardrail">
          <span>Attribution boundary</span>
          <strong>Airport activity, not premises visits</strong>
          <p>A manual link says that an address is associated with an airport base. It does not claim that every observed aircraft visited that company.</p>
        </div>
      </section>

      {error && <div className="alert alert-error" role="alert"><span>{error}</span><button type="button" onClick={() => setError(null)}>×</button></div>}
      {notice && <div className="alert alert-success" role="status"><span>{notice}</span><button type="button" onClick={() => setNotice(null)}>×</button></div>}

      <section className="locations-workspace">
        <aside className="locations-list panel">
          <div className="panel-heading"><div><p className="section-index">01 · Find an address</p><h2>Company sites</h2></div><span>{sites.length} shown</span></div>
          <form className="locations-search" onSubmit={searchSites}>
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Company, postcode, approval or site" aria-label="Search company sites" />
            <button className="button button-secondary" type="submit">Search</button>
          </form>
          <label className="locations-filter"><input type="checkbox" checked={trackedOnly} onChange={(event) => { setTrackedOnly(event.target.checked); void loadSites(query, event.target.checked); }} /> Show customers and watched addresses only</label>
          <div className={`site-results ${loading ? "site-results-loading" : ""}`}>
            {sites.map((site) => (
              <button type="button" className={`site-result ${selected?.site_id === site.site_id ? "selected" : ""}`} key={site.site_id} onClick={() => chooseSite(site)}>
                <span className="site-result-flags">{site.is_customer && <i>CUSTOMER</i>}{site.is_of_interest && <i>WATCHED</i>}{site.airport_ident && <i>LINKED</i>}</span>
                <strong>{site.company}</strong>
                <b>{site.site}</b>
                <small>{address(site) || "No imported address"}</small>
                <em>{site.approval_numbers ?? "No approval number"} · {site.capability_count} capabilities</em>
              </button>
            ))}
            {!loading && !sites.length && <p className="empty-sites">No company sites matched that search.</p>}
          </div>
        </aside>

        <div className="locations-detail">
          {!selected ? (
            <article className="panel location-empty"><span>SELECT A SITE</span><h2>Start with a company or address.</h2><p>Search on the left, then assign its airport and tracking status here.</p></article>
          ) : (
            <>
              <article className="panel location-editor">
                <div className="panel-heading"><div><p className="section-index">02 · Curate the link</p><h2>{selected.company}</h2></div><span>{selected.approval_numbers}</span></div>
                <h3>{selected.site}</h3><p className="location-address">{address(selected)}</p>
                <div className="tracking-flags">
                  <label><input type="checkbox" checked={isCustomer} onChange={(event) => setIsCustomer(event.target.checked)} /><span><strong>Customer company</strong><small>Applies to every imported site for this company.</small></span></label>
                  <label><input type="checkbox" checked={isOfInterest} onChange={(event) => setIsOfInterest(event.target.checked)} /><span><strong>Address of interest</strong><small>Keeps this particular site in the watched list.</small></span></label>
                </div>
                <label className="notes-field">Internal note<textarea value={notes} maxLength={1000} onChange={(event) => setNotes(event.target.value)} placeholder="Why this customer or address matters" /></label>
                <div className="airport-assignment">
                  <div><span>Linked airport</span>{airport ? <strong>{airport.iata_code ?? airport.ident} · {airport.name}</strong> : <strong>Not linked</strong>}</div>
                  {airport && <button type="button" className="text-button" onClick={() => setAirport(null)}>Clear link</button>}
                </div>
                <form className="airport-search" onSubmit={searchAirports}>
                  <input value={airportQuery} onChange={(event) => setAirportQuery(event.target.value)} placeholder="Airport name, IATA or ICAO" aria-label="Search airports" required minLength={2} />
                  <button className="button button-ghost" type="submit">Find airport</button>
                </form>
                {airports.length > 0 && <div className="airport-results">{airports.map((item) => <button type="button" key={item.ident} onClick={() => { setAirport(item); setAirports([]); }}><strong>{item.iata_code ?? item.ident}</strong><span>{item.name}</span><small>{item.municipality ?? item.iso_country}</small></button>)}</div>}
                <button type="button" className="button button-primary save-location" onClick={() => void save()} disabled={saving}>{saving ? "Saving…" : "Save customer and location"}</button>
              </article>

              <article className="panel location-activity">
                <div className="panel-heading"><div><p className="section-index">03 · Airport context</p><h2>Activity at the linked base</h2></div>{activityDate && <label>UTC date<input type="date" value={activityDate} onChange={(event) => { setActivityDate(event.target.value); void loadActivity(selected.site_id, event.target.value); }} /></label>}</div>
                {!selected.airport_ident && !airport ? <p className="location-prompt">Choose and save an airport to see its activity.</p> : activity?.airport_metrics ? (
                  <>
                    <p className="attribution-note">{activity.attribution_note}</p>
                    <div className="location-metrics">
                      <div><span>Unique aircraft</span><strong>{numberFormatter.format(activity.airport_metrics.unique_aircraft)}</strong></div>
                      <div><span>Arrival candidates</span><strong>{numberFormatter.format(activity.airport_metrics.arrival_candidates)}</strong></div>
                      <div><span>Departure candidates</span><strong>{numberFormatter.format(activity.airport_metrics.departure_candidates)}</strong></div>
                      <div><span>Movements</span><strong>{numberFormatter.format(activity.airport_metrics.movement_candidates)}</strong></div>
                    </div>
                  </>
                ) : <p className="location-prompt">No airport activity is available for this date.</p>}
              </article>

              {activity && <section className="location-ledgers">
                <article className="panel"><div className="panel-heading"><div><p className="section-index">Approval scope</p><h2>MRO capabilities</h2></div><span>{activity.capabilities.length} shown</span></div><div className="analytics-table-wrap"><table><thead><tr><th>Approval</th><th>Kind</th><th>Rating</th><th>Capability</th></tr></thead><tbody>{activity.capabilities.map((row, index) => <tr key={`${row.approval_number}-${index}`}><td><strong>{row.approval_number}</strong><small>{row.approval_status}</small></td><td>{row.capability_kind}</td><td>{row.rating_code ?? "—"}</td><td>{row.model ?? row.aircraft_type_code ?? row.capability ?? "—"}</td></tr>)}</tbody></table></div></article>
                <article className="panel"><div className="panel-heading"><div><p className="section-index">Observed fleet</p><h2>Leading aircraft types</h2></div></div><div className="analytics-table-wrap"><table><thead><tr><th>Type</th><th>Aircraft</th><th>Arrivals</th><th>Departures</th></tr></thead><tbody>{activity.types.slice(0, 15).map((row) => <tr key={row.type_code}><td><strong>{row.type_code}</strong><small>{row.description}</small></td><td>{row.unique_aircraft}</td><td>{row.arrival_candidates}</td><td>{row.departure_candidates}</td></tr>)}</tbody></table></div></article>
                <article className="panel location-aircraft-ledger"><div className="panel-heading"><div><p className="section-index">Observed tails</p><h2>Aircraft with movement evidence</h2></div><span>Airport-wide</span></div><div className="analytics-table-wrap"><table><thead><tr><th>Aircraft</th><th>Operator</th><th>Type</th><th>Arrivals</th><th>Departures</th><th>Evidence</th></tr></thead><tbody>{activity.aircraft.map((row) => <tr key={row.address}><td><strong>{row.registration ?? row.address.toUpperCase()}</strong><small>{row.address}</small></td><td><strong>{row.operator ?? "—"}</strong>{row.operator_source_code && <small>{row.operator_source_code}</small>}</td><td>{row.type_code ?? "—"}</td><td>{row.airport_arrival_candidates}</td><td>{row.airport_departure_candidates}</td><td>{row.activity_evidence ?? "—"}</td></tr>)}</tbody></table></div></article>
              </section>}
            </>
          )}
        </div>
      </section>
    </div>
  );
}
