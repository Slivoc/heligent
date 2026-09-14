export type TailSelection = { tail: string; from: string; to: string };

export function normalizeTail(value: string): string {
  const tail = value.trim().toUpperCase().replace(/[-\s]/g, "");
  if (!/^[A-Z0-9]{3,12}$/.test(tail)) throw new Error("Enter a valid aircraft registration");
  return tail;
}

export function readTailSelection(): TailSelection {
  const params = new URLSearchParams(typeof window === "undefined" ? "" : window.location.hash.split("?")[1]);
  return { tail: params.get("tail") || "", from: params.get("from") || "", to: params.get("to") || "" };
}

export function tailHref(surface: "aircraft" | "tracks", selection: TailSelection): string {
  const params = new URLSearchParams({ tail: selection.tail });
  if (selection.from) params.set("from", selection.from);
  if (selection.to) params.set("to", selection.to);
  return `#${surface}?${params}`;
}

export function elapsed(seconds: number): string {
  if (seconds >= 172800) return `${(seconds / 86400).toFixed(2)} days`;
  if (seconds >= 3600) return `${(seconds / 3600).toFixed(1)} hr`;
  return seconds >= 60 ? `${Math.round(seconds / 60)} min` : `${Math.round(seconds)} sec`;
}

export function utcTime(value: string): string {
  return new Date(value).toISOString().replace("T", " ").slice(0, 16) + " UTC";
}
