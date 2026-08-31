import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

async function render(pathname = "/") {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  return worker.fetch(
    new Request(`http://localhost${pathname}`, { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the Phase 5 natural-language analytics product", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<title>Heligent - Ask the aviation data<\/title>/i);
  assert.match(html, /Ask the aviation data/);
  assert.match(html, /Ask anything about the available ADS-B history/);
  assert.match(html, /Coverage first/);
  assert.match(html, /Semantic SQL/);
  assert.match(html, /server parses, limits and cost-checks every query/);
  assert.match(html, /Save debug details/);
  assert.match(html, /Explorer/);
  assert.match(html, /Operators/);
  assert.match(html, /Data control/);
  assert.doesNotMatch(html, /codex-preview|Your site is taking shape|react-loading-skeleton/i);
});

test("server-renders the focused mobile demo without admin controls", async () => {
  const response = await render("/demo");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /HELIGENT/);
  assert.match(html, /Ask the aviation data/);
  assert.match(html, /Ask Heligent/);
  assert.doesNotMatch(html, /Data control|Explorer|Save debug details|Manage local coverage/);
});

test("ships the Flask-served SPA bundle", async () => {
  const indexUrl = new URL("../../src/adsb_ingest/web_static/index.html", import.meta.url);
  await access(indexUrl);
  const html = await readFile(indexUrl, "utf8");
  assert.match(html, /<title>Heligent - Ask the aviation data<\/title>/i);
  assert.match(html, /<div id="root"><\/div>/);
  assert.match(html, /assets\/[^"']+\.js/);
  assert.match(html, /assets\/[^"']+\.css/);
});

test("starter preview code and metadata are removed", async () => {
  const [page, layout, packageJson] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
  ]);
  assert.match(page, /Dashboard/);
  assert.match(layout, /Ask the aviation data/);
  assert.doesNotMatch(page + layout + packageJson, /_sites-preview|codex-preview|react-loading-skeleton/);
});
