"use client";

import {
  ArcElement,
  BarController,
  BarElement,
  CategoryScale,
  Chart as ChartJS,
  DoughnutController,
  Legend,
  LinearScale,
  LineController,
  LineElement,
  PointElement,
  Tooltip,
  type ChartOptions,
} from "chart.js";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { Bar, Doughnut, Line } from "react-chartjs-2";
import { OperatorPicker } from "./OperatorPicker";

ChartJS.register(
  ArcElement,
  BarController,
  BarElement,
  CategoryScale,
  DoughnutController,
  Legend,
  LinearScale,
  LineController,
  LineElement,
  PointElement,
  Tooltip,
);

type Availability = {
  earliest_available_date?: string | null;
  latest_available_date?: string | null;
  available_days: number;
  available_dates: string[];
};

export type QueryConfig = {
  configured: boolean;
  model: string;
  availability: Availability;
  examples: string[];
};

export type QueryResult = {
  question: string;
  plan?: {
    operation?: string;
    metric?: string;
    title?: string;
    result_kind?: "ranking" | "time_series" | "table" | "single_value";
    from_date: string | null;
    to_date: string | null;
    type_code?: string | null;
    helicopters_only?: boolean;
    assignment_role?: string;
  } | null;
  coverage: {
    requested_days: number;
    available_days: number;
    complete: boolean;
    available_dates?: string[];
    missing_dates?: string[];
    message: string;
  };
  answer: string;
  table: { columns: string[]; rows: Array<Record<string, unknown>> };
  chart?: {
    type: "line" | "bar" | "doughnut";
    x: string;
    y: string;
    label: string;
  } | null;
  suggestions: string[];
  meta?: { model?: string; execution?: string };
  debug_dump?: { id: string; path: string };
};

type Exchange = {
  id: number;
  question: string;
  result?: QueryResult;
  error?: string;
};

type AskDataProps = {
  onOpenData: () => void;
};

const numberFormatter = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 1 });
const colours = ["#59d6c5", "#3975e8", "#e2a534", "#8a76d6", "#d96760", "#74a85e", "#4ba4c4", "#c88951"];

function apiBase(): string {
  if (typeof window !== "undefined" && window.location.port === "3000") {
    return "http://127.0.0.1:5080";
  }
  return "";
}

export async function getConfig(): Promise<QueryConfig> {
  const response = await fetch(`${apiBase()}/api/query/config`, { cache: "no-store" });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error ?? "Unable to load query configuration");
  return payload as QueryConfig;
}

export async function ask(question: string, context: string[], debug: boolean): Promise<QueryResult> {
  let response: Response;
  try {
    response = await fetch(`${apiBase()}/api/query`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "HeligentQuery" },
      body: JSON.stringify({ question, context, debug }),
      signal: AbortSignal.timeout(50_000),
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "TimeoutError") {
      throw new Error("The query took longer than 50 seconds. Please try it again or use a shorter date range.");
    }
    throw error;
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error ?? `The query failed (${response.status})`);
  return payload as QueryResult;
}

export function fieldLabel(value: string): string {
  const labels: Record<string, string> = {
    utc_date: "UTC date",
    airport: "Hub",
    airport_name: "Airport",
    type_code: "Type",
    description: "Description",
    tail: "Tail",
    operator: "Operator",
    operator_source_code: "Operator source",
    primary_airport_label: "Primary hub",
    unique_aircraft: "Unique aircraft",
    observations: "Observations",
    active_hours: "Active hours",
    airborne_hours: "Airborne hours",
    ground_active_hours: "Ground-active hours",
    ground_observations: "Ground observations",
    airport_ground_observations: "Airport ground observations",
    airport_ground_active_hours: "Airport ground-active hours",
    movement_candidates: "Movement candidates",
    arrival_candidates: "Arrival candidates",
    departure_candidates: "Departure candidates",
    airport_movement_candidates: "Airport movement candidates",
    airport_arrival_candidates: "Airport arrival candidates",
    airport_departure_candidates: "Airport departure candidates",
    activity_evidence: "Activity evidence",
    primary_aircraft: "Primary aircraft",
    primary_tail_active_hours: "Primary-tail hours",
    primary_tail_airborne_hours: "Primary-tail airborne",
    airport_linked_aircraft: "Hub-linked aircraft",
    known_type_codes: "Known type codes",
    active_days: "Active days",
    metric: "Metric",
    value: "Value",
  };
  return labels[value] ?? value.replaceAll("_", " ");
}

export function formatCell(value: unknown, field: string): string {
  if (value == null || value === "") return "—";
  if (typeof value === "number") {
    if (field.endsWith("hours")) return `${numberFormatter.format(value)} h`;
    return numberFormatter.format(value);
  }
  if (field === "utc_date" && typeof value === "string") {
    return new Intl.DateTimeFormat("en-GB", {
      day: "2-digit",
      month: "short",
      year: "numeric",
      timeZone: "UTC",
    }).format(new Date(`${value}T12:00:00Z`));
  }
  return String(value);
}

function ResultChart({ result }: { result: QueryResult }) {
  const chart = result.chart;
  const data = useMemo(() => {
    if (!chart) return null;
    const rows = result.table.rows;
    return {
      labels: rows.map((row) => formatCell(row[chart.x], chart.x)),
      datasets: [{
        label: chart.label,
        data: rows.map((row) => Number(row[chart.y] ?? 0)),
        borderColor: chart.type === "line" ? "#59d6c5" : "#173e52",
        backgroundColor: chart.type === "doughnut" ? colours : chart.type === "line" ? "rgba(89,214,197,.16)" : "rgba(89,214,197,.76)",
        borderWidth: chart.type === "line" ? 2 : 1,
        fill: chart.type === "line",
        tension: 0.28,
        pointRadius: chart.type === "line" ? 3 : 0,
      }],
    };
  }, [chart, result.table.rows]);

  if (!chart || !data) return null;
  const cartesianOptions: ChartOptions<"bar" | "line"> = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { display: false },
      tooltip: { intersect: false, mode: "index" },
    },
    scales: {
      x: { grid: { display: false }, ticks: { color: "#6d7d87", maxRotation: 0, autoSkip: true } },
      y: { beginAtZero: true, grid: { color: "rgba(16,43,60,.08)" }, ticks: { color: "#6d7d87" } },
    },
  };
  const doughnutOptions: ChartOptions<"doughnut"> = {
    responsive: true,
    maintainAspectRatio: false,
    cutout: "58%",
    plugins: { legend: { position: "right", labels: { boxWidth: 10, color: "#526474" } } },
  };

  if (chart.type === "line") return <Line data={data} options={cartesianOptions as ChartOptions<"line">} />;
  if (chart.type === "doughnut") return <Doughnut data={data} options={doughnutOptions} />;
  return <Bar data={data} options={cartesianOptions as ChartOptions<"bar">} />;
}

function ResultCard({ exchange, onSuggestion, onOperatorSaved }: {
  exchange: Exchange;
  onSuggestion: (value: string) => void;
  onOperatorSaved: (rowIndex: number, column: string, operator: string, sourceCode: string) => void;
}) {
  if (exchange.error) {
    return <div className="ask-error" role="alert"><strong>Couldn’t answer that yet</strong><p>{exchange.error}</p></div>;
  }
  const result = exchange.result;
  if (!result) return <div className="query-thinking" role="status"><span /><p>Reading the question and selecting a safe analytics view…</p></div>;
  const planSummary = result.plan?.title
    ? [
        result.plan.result_kind?.replaceAll("_", " "),
        result.plan.from_date && result.plan.to_date
          ? `${result.plan.from_date} → ${result.plan.to_date}`
          : "reference data",
      ].filter(Boolean).join(" · ")
    : result.plan?.operation
      ? [
          result.plan.operation.replaceAll("_", " "),
          result.plan.metric ? fieldLabel(result.plan.metric) : null,
          result.plan.from_date && result.plan.to_date
            ? `${result.plan.from_date} → ${result.plan.to_date}`
            : null,
        ].filter(Boolean).join(" · ")
      : null;

  return (
    <article className="answer-card">
      <header className="answer-heading">
        <div><span className="answer-mark">H</span><p>{result.answer}</p></div>
        <span className="answer-model">{result.meta?.model ?? "curated analytics"}</span>
      </header>
      <div className={`answer-coverage ${result.coverage.complete ? "coverage-ok" : "coverage-partial"}`}>
        <span>{result.coverage.complete ? "✓" : "!"}</span>
        <p><strong>{result.coverage.requested_days > 0 ? `${result.coverage.available_days} / ${result.coverage.requested_days} UTC days available` : "Reference data"}</strong>{result.coverage.message}</p>
      </div>
      {result.chart && <div className="answer-chart"><ResultChart result={result} /></div>}
      {result.table.rows.length > 0 && (
        <div className="answer-table-wrap">
          <table>
            <thead><tr>{result.table.columns.map((column) => <th key={column}>{fieldLabel(column)}</th>)}</tr></thead>
            <tbody>{result.table.rows.map((row, index) => (
              <tr key={`${exchange.id}-${index}`}>{result.table.columns.map((column) => {
                const address = typeof row.address === "string" ? row.address : typeof row.aircraft_address === "string" ? row.aircraft_address : null;
                const registration = typeof row.registration === "string" ? row.registration : null;
                const isOperator = column === "operator" || (column === "company" && row.assignment_role === "OPERATOR");
                return <td key={column}>{isOperator && address && /^[0-9a-f]{6}$/i.test(address)
                  ? <OperatorPicker address={address} registration={registration} operator={typeof row[column] === "string" ? row[column] : null} onSaved={(operator, sourceCode) => onOperatorSaved(index, column, operator, sourceCode)} />
                  : formatCell(row[column], column)}</td>;
              })}</tr>
            ))}</tbody>
          </table>
        </div>
      )}
      {planSummary && (
        <div className="answer-provenance">
          <span>Validated query</span>
          <code>{planSummary}</code>
        </div>
      )}
      {result.debug_dump && (
        <div className="debug-dump-note">
          <span>Debug saved</span>
          <code>{result.debug_dump.path}</code>
          <p>Give Codex this filename to inspect the plan, corrections, coverage and candidate rows.</p>
        </div>
      )}
      {result.suggestions.length > 0 && (
        <div className="follow-ups"><span>Ask a follow-up</span>{result.suggestions.map((item) => <button type="button" key={item} onClick={() => onSuggestion(item)}>{item}</button>)}</div>
      )}
    </article>
  );
}

export function AskData({ onOpenData }: AskDataProps) {
  const [config, setConfig] = useState<QueryConfig | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const [working, setWorking] = useState(false);
  const [captureDebug, setCaptureDebug] = useState(false);

  useEffect(() => {
    let active = true;
    void getConfig()
      .then((value) => { if (active) setConfig(value); })
      .catch((error) => { if (active) setConfigError(error instanceof Error ? error.message : "Unable to load configuration"); });
    return () => { active = false; };
  }, []);

  const submitQuestion = async (value: string) => {
    const clean = value.trim();
    if (clean.length < 4 || working) return;
    const id = Date.now();
    const context = exchanges.map((item) => item.question).slice(-4);
    setQuestion("");
    setWorking(true);
    setExchanges((current) => [...current, { id, question: clean }]);
    try {
      const result = await ask(clean, context, captureDebug);
      setExchanges((current) => current.map((item) => item.id === id ? { ...item, result } : item));
    } catch (error) {
      setExchanges((current) => current.map((item) => item.id === id ? {
        ...item,
        error: error instanceof Error ? error.message : "The question could not be answered",
      } : item));
    } finally {
      setWorking(false);
    }
  };

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    void submitQuestion(question);
  };

  const updateResultOperator = (exchangeId: number, rowIndex: number, column: string, operator: string, sourceCode: string) => {
    setExchanges(current => current.map(item => {
      if (item.id !== exchangeId || !item.result) return item;
      const rows = item.result.table.rows.map((row, index) => index === rowIndex ? {
        ...row,
        [column]: operator,
        operator_source_code: sourceCode,
        ...(column === "company" ? { source_code: sourceCode } : {}),
      } : row);
      return { ...item, result: { ...item.result, table: { ...item.result.table, rows } } };
    }));
  };

  const latest = config?.availability.latest_available_date;
  const empty = exchanges.length === 0;

  return (
    <section className={`ask-surface ${empty ? "ask-empty" : "ask-conversation"}`}>
      <div className="ask-grid" aria-hidden="true" />
      <div className="ask-intro">
        <p className="ask-eyebrow"><span /> Phase 5 · Natural-language analytics</p>
        <h1>Ask the aviation data.</h1>
        <p>Explore hub traffic, individual-tail activity hours, aircraft types and classified helicopters across the history stored on this machine.</p>
        <div className="availability-line">
          <span className="availability-dot" />
          {config ? <><strong>{config.availability.available_days} local day{config.availability.available_days === 1 ? "" : "s"}</strong><span>{latest ? `through ${latest}` : "— add a day to begin"}</span></> : <span>Reading local coverage…</span>}
        </div>
      </div>

      {!config?.configured && config && (
        <div className="api-key-note" role="status">
          <span>API setup</span>
          <p><strong>The question planner is ready but not connected.</strong> Set <code>OPENAI_API_KEY</code> in the server environment and restart this local app. The key stays server-side.</p>
        </div>
      )}
      {configError && <div className="api-key-note api-key-error" role="alert"><span>Connection</span><p>{configError}</p></div>}

      <div className="conversation-stream" aria-live="polite">
        {exchanges.map((exchange) => (
          <div className="exchange" key={exchange.id}>
            <div className="user-question"><span>You asked</span><p>{exchange.question}</p></div>
            <ResultCard exchange={exchange} onSuggestion={(value) => void submitQuestion(value)} onOperatorSaved={(rowIndex, column, operator, sourceCode) => updateResultOperator(exchange.id, rowIndex, column, operator, sourceCode)} />
          </div>
        ))}
      </div>

      <form className="ask-composer" onSubmit={onSubmit}>
        <label htmlFor="aviation-question">Ask anything about the available ADS-B history</label>
        <div className="composer-row">
          <textarea
            id="aviation-question"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void submitQuestion(question);
              }
            }}
            placeholder="e.g. Which hubs saw the most unique aircraft on the latest available day?"
            rows={2}
            maxLength={600}
            disabled={working}
          />
          <button type="submit" disabled={working || question.trim().length < 4} aria-label="Ask the aviation data">
            {working ? <span className="button-loader" /> : <span aria-hidden="true">→</span>}
          </button>
        </div>
        <div className="composer-meta"><span>Enter to ask · Shift + Enter for a new line</span><span>Model plans · server validates · PostgreSQL answers</span></div>
        <label className="debug-toggle">
          <input type="checkbox" checked={captureDebug} onChange={(event) => setCaptureDebug(event.target.checked)} />
          <span><strong>Save debug details</strong>Writes a redacted local JSON dump for review.</span>
        </label>
      </form>

      {empty && config?.examples.length ? (
        <div className="example-questions">
          <span>Try a real question</span>
          <div>{config.examples.map((example) => <button type="button" key={example} onClick={() => setQuestion(example)}>{example}<i>↗</i></button>)}</div>
        </div>
      ) : null}

      {empty && (
        <div className="ask-guardrails">
          <div><span>01</span><p><strong>Coverage first</strong>Partial date ranges are always called out.</p></div>
          <div><span>02</span><p><strong>Semantic SQL</strong>The model sees safe aviation views; the server parses, limits and cost-checks every query.</p></div>
          <div><span>03</span><p><strong>Constrained output</strong>Charts are limited to safe, known specifications.</p></div>
          <button type="button" onClick={onOpenData}>Manage local coverage →</button>
        </div>
      )}
    </section>
  );
}
