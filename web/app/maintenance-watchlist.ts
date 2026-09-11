export type Watch = {
  id: number;
  registration: string;
  notes?: string;
  operator?: string | null;
  type_code?: string | null;
  last_seen_date?: string | null;
  last_confirmed_maintenance?: string | null;
};

export type WatchFilters = { search: string; operator: string; type: string; review: string };
export const emptyWatchFilters: WatchFilters = { search: "", operator: "", type: "", review: "" };
export const unknownValue = "__unknown__";
const normalizeTail = (value: string) => value.toUpperCase().replace(/[-\s]/g, "");

export function filterWatches(watches: Watch[], filters: WatchFilters) {
  const query = filters.search.trim().toLowerCase();
  return watches.filter(watch => {
    const matchesSearch = !query || normalizeTail(watch.registration).includes(normalizeTail(query))
      || [watch.operator, watch.type_code].some(value => value?.toLowerCase().includes(query));
    return matchesSearch
      && (!filters.operator || (watch.operator || unknownValue) === filters.operator)
      && (!filters.type || (watch.type_code || unknownValue) === filters.type)
      && (!filters.review || (filters.review === "reviewed" ? !!watch.last_confirmed_maintenance : !watch.last_confirmed_maintenance));
  });
}

export function watchOptions(watches: Watch[], field: "operator" | "type_code") {
  return [...new Set(watches.map(watch => watch[field]).filter((value): value is string => !!value))]
    .sort((a, b) => a.localeCompare(b));
}
