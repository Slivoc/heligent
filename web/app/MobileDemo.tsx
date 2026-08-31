"use client";

import { FormEvent, useEffect, useState } from "react";
import {
  ask,
  fieldLabel,
  formatCell,
  getConfig,
  type QueryConfig,
  type QueryResult,
} from "./AskData";

export function MobileDemo() {
  const [config, setConfig] = useState<QueryConfig | null>(null);
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<QueryResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const [history, setHistory] = useState<string[]>([]);

  useEffect(() => {
    let active = true;
    void getConfig()
      .then((value) => {
        if (active) setConfig(value);
      })
      .catch((reason) => {
        if (active) {
          setError(reason instanceof Error ? reason.message : "Unable to connect to Heligent");
        }
      });
    return () => {
      active = false;
    };
  }, []);

  const submitQuestion = async (value: string) => {
    const clean = value.trim();
    if (clean.length < 4 || working) return;
    setQuestion("");
    setResult(null);
    setError(null);
    setWorking(true);
    try {
      const response = await ask(clean, history.slice(-4), false);
      setResult(response);
      setHistory((current) => [...current, clean].slice(-4));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "That question could not be answered");
    } finally {
      setWorking(false);
    }
  };

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    void submitQuestion(question);
  };

  const suggestions = result?.suggestions.length
    ? result.suggestions
    : config?.examples.slice(0, 3) ?? [];

  return (
    <main className="mobile-demo-shell">
      <div className="mobile-demo-glow" aria-hidden="true" />
      <header className="mobile-demo-brand">
        <span className="mobile-demo-mark" aria-hidden="true"><i>H</i></span>
        <span>
          <strong>HELIGENT</strong>
          <small>ADS-B HUB INTELLIGENCE</small>
        </span>
      </header>

      <section className="mobile-demo-conversation" aria-live="polite">
        {!result && !error && !working && (
          <div className="mobile-demo-welcome">
            <h1>Ask the aviation data.</h1>
          </div>
        )}

        {working && (
          <div className="mobile-demo-thinking" role="status">
            <span className="button-loader" />
            <p>Reading the data...</p>
          </div>
        )}

        {error && (
          <div className="mobile-demo-error" role="alert">
            <strong>Couldn&apos;t answer that</strong>
            <p>{error}</p>
          </div>
        )}

        {result && (
          <article className="mobile-demo-answer">
            <p>{result.answer}</p>
            {result.table.rows.length > 0 && (
              <div className="mobile-demo-table-wrap">
                <table>
                  <thead>
                    <tr>{result.table.columns.map((column) => <th key={column}>{fieldLabel(column)}</th>)}</tr>
                  </thead>
                  <tbody>
                    {result.table.rows.map((row, rowIndex) => (
                      <tr key={rowIndex}>
                        {result.table.columns.map((column) => (
                          <td key={column}>{formatCell(row[column], column)}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </article>
        )}
      </section>

      <form className="mobile-demo-composer" onSubmit={onSubmit}>
        <label htmlFor="mobile-demo-question">Ask Heligent</label>
        <div>
          <textarea
            id="mobile-demo-question"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void submitQuestion(question);
              }
            }}
            placeholder="Which helicopter types were seen at ABZ?"
            rows={2}
            maxLength={600}
            disabled={working}
          />
          <button
            type="submit"
            disabled={working || question.trim().length < 4}
            aria-label="Ask Heligent"
          >
            <span aria-hidden="true">↑</span>
          </button>
        </div>
      </form>

      {suggestions.length > 0 && !working && (
        <nav className="mobile-demo-suggestions" aria-label="Suggested questions">
          {suggestions.slice(0, 3).map((suggestion) => (
            <button type="button" key={suggestion} onClick={() => void submitQuestion(suggestion)}>
              {suggestion}
            </button>
          ))}
        </nav>
      )}
    </main>
  );
}
