export type EnterprisePlan = {
  plan_id: string;
  capabilities: Record<string, boolean>;
  hardware_class: string;
  telemetry_retention_days: number;
};

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

/** Rejects the OSS feature summary and malformed Enterprise responses safely. */
export function normalizeEnterprisePlan(value: unknown): EnterprisePlan | null {
  const candidate = record(value);
  const rawCapabilities = record(candidate?.capabilities);
  if (!candidate || !rawCapabilities) return null;

  const capabilities = Object.fromEntries(
    Object.entries(rawCapabilities).filter((entry): entry is [string, boolean] => typeof entry[1] === 'boolean'),
  );
  return {
    plan_id: typeof candidate.plan_id === 'string' ? candidate.plan_id : 'enterprise',
    capabilities,
    hardware_class: typeof candidate.hardware_class === 'string' ? candidate.hardware_class : '',
    telemetry_retention_days: typeof candidate.telemetry_retention_days === 'number'
      ? candidate.telemetry_retention_days
      : 0,
  };
}

export function planHasCapability(plan: EnterprisePlan | null, capability: string): boolean {
  return plan?.capabilities?.[capability] === true;
}
