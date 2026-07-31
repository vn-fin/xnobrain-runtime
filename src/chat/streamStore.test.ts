import { describe, expect, it } from 'vitest';
import { streamErrorMessage } from './streamStore';

describe('streamErrorMessage', () => {
  it('extracts a nested provider message instead of rendering an object', () => {
    expect(streamErrorMessage({ error: { message: 'Invalid API key' } })).toBe('Invalid API key');
  });

  it('turns quota failures into an actionable message', () => {
    expect(streamErrorMessage({ error: { code: 'insufficient_quota' } })).toContain('out of quota');
  });

  it('turns expired subscriptions into an actionable message', () => {
    expect(streamErrorMessage({ detail: 'Subscription has expired' })).toContain('subscription or license has expired');
  });
});
