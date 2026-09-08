export function orderedStops<T extends { number:number; ground_time_seconds:number }>(stops:T[],order:string):T[] {
  return [...stops].sort((a,b)=>order==='GROUND' ? b.ground_time_seconds-a.ground_time_seconds || a.number-b.number : order==='NEWEST' ? b.number-a.number : a.number-b.number);
}

export function maintenanceLeads<T extends {airport_ident:string|null}>(sites:T[],airport:string):T[] {
  return sites.filter(site=>site.airport_ident===airport);
}
