type Wording = { model: string | null; aircraft_type_code: string | null; limitation: string | null };

export function CapabilityWording({ capability }: { capability: Wording }) {
  return <>
    <p><strong>Aircraft model: </strong>{capability.model?.trim() || 'Not separately recorded'}</p>
    {capability.aircraft_type_code?.trim() && <p><strong>Recorded ICAO type: </strong>{capability.aircraft_type_code}</p>}
    <p><strong>Scope / original wording: </strong>{capability.limitation?.trim() || 'Not supplied'}</p>
  </>;
}
