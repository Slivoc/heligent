"use client";

import { FormEvent, useEffect, useId, useState } from "react";

const apiBase = () => typeof window !== "undefined" && window.location.port === "3000" ? "http://127.0.0.1:5080" : "";

export function OperatorPicker({ address, registration, operator, onSaved }: {
  address: string;
  registration?: string | null;
  operator?: string | null;
  onSaved?: (operator: string, sourceCode: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(operator ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastSaved, setLastSaved] = useState<{ previous: string | null; value: string } | null>(null);
  const [suggestions, setSuggestions] = useState<{ query: string; names: string[] }>({ query: "", names: [] });
  const [suggestionsOpen, setSuggestionsOpen] = useState(false);
  const [activeSuggestion, setActiveSuggestion] = useState(-1);
  const listboxId = `operator-options-${address}-${useId().replaceAll(":", "")}`;
  const normalizedQuery = value.trim().toLowerCase();
  const displayedOperator = lastSaved?.previous === (operator ?? null) ? lastSaved.value : operator;

  useEffect(() => {
    if (!editing || normalizedQuery.length < 2) return;
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      try {
        const response = await fetch(`${apiBase()}/api/operators/search?q=${encodeURIComponent(value.trim())}`, { signal: controller.signal, headers: { "X-Requested-With": "HeligentAdmin" } });
        const result = await response.json();
        if (response.ok) setSuggestions({ query: normalizedQuery, names: result.operators.map((row: { name: string }) => row.name) });
      } catch (exc) {
        if (!(exc instanceof DOMException && exc.name === "AbortError")) setSuggestions({ query: normalizedQuery, names: [] });
      }
    }, 180);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [editing, normalizedQuery, value]);

  const visibleSuggestions = suggestionsOpen && suggestions.query === normalizedQuery ? suggestions.names : [];
  const chooseSuggestion = (name: string) => { setValue(name); setSuggestionsOpen(false); setActiveSuggestion(-1); };
  const submitOperator = async (event: FormEvent) => {
    event.preventDefault();
    if (!value.trim()) return;
    setSaving(true); setError(null);
    try {
      const response = await fetch(`${apiBase()}/api/aircraft/${address}/operator`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json", "X-Requested-With": "HeligentAdmin" },
        body: JSON.stringify({ operator: value.trim(), registration }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error ?? "Unable to save operator");
      const saved = result.assignment.operator as string;
      const sourceCode = result.assignment.operator_source_code as string;
      setLastSaved({ previous: operator ?? null, value: saved });
      setValue(saved); setEditing(false);
      onSaved?.(saved, sourceCode);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Unable to save operator");
    } finally { setSaving(false); }
  };

  if (!editing) return <div className="tail-operator"><strong>{displayedOperator ?? "—"}</strong><button type="button" onClick={() => { setValue(displayedOperator ?? ""); setEditing(true); }} aria-label={`Edit operator for ${registration ?? address}`}>{displayedOperator ? "Edit" : "+ Add operator"}</button></div>;
  return <form className="tail-operator-form" onSubmit={submitOperator}>
    <div className="operator-combobox">
      <input autoFocus value={value} onChange={event => { setValue(event.target.value); setSuggestionsOpen(true); setActiveSuggestion(-1); }} onFocus={() => setSuggestionsOpen(true)} onBlur={() => window.setTimeout(() => setSuggestionsOpen(false), 120)} onKeyDown={event => {
        if (event.key === "ArrowDown" && visibleSuggestions.length) { event.preventDefault(); setActiveSuggestion(current => Math.min(current + 1, visibleSuggestions.length - 1)); }
        else if (event.key === "ArrowUp" && visibleSuggestions.length) { event.preventDefault(); setActiveSuggestion(current => Math.max(current - 1, 0)); }
        else if (event.key === "Enter" && activeSuggestion >= 0) { event.preventDefault(); chooseSuggestion(visibleSuggestions[activeSuggestion]); }
        else if (event.key === "Escape") setSuggestionsOpen(false);
      }} placeholder="Search operators" aria-label={`Operator for ${registration ?? address}`} role="combobox" aria-autocomplete="list" aria-expanded={Boolean(visibleSuggestions.length)} aria-controls={listboxId} maxLength={200} required />
      {visibleSuggestions.length > 0 && <div className="operator-suggestions" id={listboxId} role="listbox">{visibleSuggestions.map((name, index) => <button key={name} type="button" role="option" aria-selected={index === activeSuggestion} className={index === activeSuggestion ? "active" : ""} onMouseDown={event => { event.preventDefault(); chooseSuggestion(name); }}>{name}</button>)}</div>}
    </div>
    <div className="tail-operator-actions"><button type="submit" disabled={saving}>{saving ? "Saving…" : "Save"}</button><button type="button" disabled={saving} onClick={() => { setEditing(false); setError(null); }}>Cancel</button></div>
    {error && <small role="alert">{error}</small>}
  </form>;
}
