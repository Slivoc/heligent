"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

type FleetSummary = {
  current_assignments: number;
  operator_assignments: number;
  companies_with_assignments: number;
  unique_aircraft: number;
  conflicting_aircraft: number;
};

type AircraftAssignment = {
  company: string;
  legal_name?: string | null;
  trading_name?: string | null;
  country?: string | null;
  registration?: string | null;
  aircraft_address?: string | null;
  assignment_role: string;
  confidence?: number | null;
  valid_from?: string | null;
  valid_to?: string | null;
  source_code: string;
  source_assignment_id?: string | null;
};

type OperatorResponse = {
  summary: FleetSummary;
  assignments: AircraftAssignment[];
};

const numberFormatter = new Intl.NumberFormat("en-GB");

function apiBase(): string {
  return typeof window !== "undefined" && window.location.port === "3000"
    ? "http://127.0.0.1:5080"
    : "";
}

function formatRole(value: string): string {
  return value.replaceAll("_", " ").toLowerCase().replace(/^./, (letter) => letter.toUpperCase());
}

function formatConfidence(value?: number | null): string {
  return value == null ? "—" : `${Math.round(value * 100)}%`;
}

function formatValidity(from?: string | null, to?: string | null): string {
  if (!from && !to) return "Current / undated";
  if (from && to) return `${from} to ${to}`;
  return from ? `From ${from}` : `Until ${to}`;
}

export function OperatorFleet() {
  const [query, setQuery] = useState("");
  const [role, setRole] = useState("OPERATOR");
  const [data, setData] = useState<OperatorResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (search = "", assignmentRole = "OPERATOR") => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ role: assignmentRole, limit: "100" });
      if (search.trim()) params.set("q", search.trim());
      const response = await fetch(`${apiBase()}/api/operators?${params}`, {
        headers: { "X-Requested-With": "HeligentAdmin" },
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error ?? `Request failed (${response.status})`);
      setData(payload as OperatorResponse);
      setError(null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Unable to load operator assignments");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void load(query, role);
  };

  const changeRole = (value: string) => {
    setRole(value);
    void load(query, value);
  };

  const summary = data?.summary;
  const assignments = data?.assignments ?? [];

  return (
    <div className="operators-surface">
      <section className="operators-hero">
        <div>
          <p className="eyebrow">Imported operator intelligence</p>
          <h1>Trace each tail to its sourced operator claim.</h1>
          <p>Search the aircraft assignments imported from your CRM and other reference sources. Every row keeps its role, confidence, validity, and source identifier visible.</p>
        </div>
        <aside className="operators-guardrail">
          <span>Evidence boundary</span>
          <strong>Assignment data, not flight attribution</strong>
          <p>These records say what a named source reported. They do not, on their own, prove who controlled a particular flight.</p>
        </aside>
      </section>

      <section className="operator-metrics" aria-label="Operator assignment summary">
        <article><span>Current assignments</span><strong>{numberFormatter.format(summary?.current_assignments ?? 0)}</strong><small>All imported roles</small></article>
        <article><span>Operator claims</span><strong>{numberFormatter.format(summary?.operator_assignments ?? 0)}</strong><small>Current OPERATOR rows</small></article>
        <article><span>Companies</span><strong>{numberFormatter.format(summary?.companies_with_assignments ?? 0)}</strong><small>With a current assignment</small></article>
        <article><span>Conflicting tails</span><strong>{numberFormatter.format(summary?.conflicting_aircraft ?? 0)}</strong><small>Assigned to multiple companies</small></article>
      </section>

      <section className="operator-workspace panel">
        <div className="operator-heading">
          <div><p className="section-index">Current sourced records</p><h2>Aircraft assignments</h2></div>
          <span>{loading ? "Loading…" : `${assignments.length} shown · 100 maximum`}</span>
        </div>

        <form className="operator-search" onSubmit={submit}>
          <label>
            Search records
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Operator, registration, Mode S or source" />
          </label>
          <label>
            Assignment role
            <select value={role} onChange={(event) => changeRole(event.target.value)}>
              <option value="OPERATOR">Operator</option>
              <option value="OWNER">Owner</option>
              <option value="MANAGER">Manager</option>
              <option value="AOC_AUTHORIZED">AOC authorised</option>
              <option value="OTHER">Other</option>
              <option value="ANY">All roles</option>
            </select>
          </label>
          <button className="button button-primary" type="submit" disabled={loading}>Search</button>
        </form>

        {error && <div className="alert alert-error" role="alert"><span>{error}</span><button type="button" onClick={() => setError(null)} aria-label="Dismiss error">×</button></div>}

        <div className={`operator-table-wrap ${loading ? "operator-table-loading" : ""}`}>
          <table>
            <thead><tr><th>Aircraft</th><th>Operator / company</th><th>Role</th><th>Confidence</th><th>Validity</th><th>Source</th></tr></thead>
            <tbody>
              {assignments.map((row, index) => (
                <tr key={`${row.source_code}-${row.source_assignment_id ?? index}`}>
                  <td><strong>{row.registration ?? row.aircraft_address?.toUpperCase() ?? "Unknown"}</strong>{row.registration && row.aircraft_address && <small>{row.aircraft_address.toUpperCase()}</small>}</td>
                  <td><strong>{row.company}</strong><small>{row.trading_name ?? row.legal_name ?? row.country ?? "—"}</small></td>
                  <td><span className="operator-role">{formatRole(row.assignment_role)}</span></td>
                  <td>{formatConfidence(row.confidence)}</td>
                  <td>{formatValidity(row.valid_from, row.valid_to)}</td>
                  <td><strong>{row.source_code}</strong><small>{row.source_assignment_id ?? "No source row ID"}</small></td>
                </tr>
              ))}
            </tbody>
          </table>
          {!loading && !assignments.length && <p className="empty-operators">No current assignments match these filters.</p>}
        </div>
        <p className="operator-footnote">Natural-language questions use this same current-assignment dataset. Try “Who operates G-TEST?” or “Which aircraft are assigned to Bristow as operator?” in Ask data.</p>
      </section>
    </div>
  );
}
