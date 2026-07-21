import { describe, expect, it } from 'vitest';
import { normalizeEnterprisePlan, planHasCapability } from './authPlan';

describe('enterprise plan normalization', () => {
  it('does not treat the OSS feature summary as an enterprise plan', () => {
    const plan = normalizeEnterprisePlan({ available: false, local_features_unrestricted: true });

    expect(plan).toBeNull();
    expect(planHasCapability(plan, 'managed_telemetry')).toBe(false);
  });

  it('preserves boolean capabilities from a valid enterprise plan', () => {
    const plan = normalizeEnterprisePlan({
      plan_id: 'pro',
      capabilities: { managed_telemetry: true, managed_devices: false, malformed: 'yes' },
      hardware_class: 'standard',
      telemetry_retention_days: 30,
    });

    expect(plan).toEqual({
      plan_id: 'pro',
      capabilities: { managed_telemetry: true, managed_devices: false },
      hardware_class: 'standard',
      telemetry_retention_days: 30,
    });
    expect(planHasCapability(plan, 'managed_telemetry')).toBe(true);
    expect(planHasCapability(plan, 'managed_devices')).toBe(false);
  });
});
