import { describe, expect, it } from 'vitest';
import { computeUrl, parseRoute, type RouteState } from './useRouter';

const state = (settingsSection: RouteState['settingsSection']): RouteState => ({
  centerView: 'data',
  settingsSection,
  agentId: '',
  conversationId: '',
  rightView: 'workspace',
  agentSearch: '',
  skillsSearch: '',
  skillsGroupFilter: 'all',
});

describe('settings routes', () => {
  it.each([
    ['/settings/profiles', 'profiles'],
    ['/settings/vm', 'vm'],
    ['/settings/connectors', 'connectors'],
  ] as const)('parses %s', (path, section) => {
    expect(parseRoute(path, '').settingsSection).toBe(section);
    expect(computeUrl(state(section))).toBe(path);
  });

  it('redirects legacy settings destinations to their matching section', () => {
    expect(parseRoute('/sandbox', '').settingsSection).toBe('vm');
    expect(parseRoute('/connections', '').settingsSection).toBe('connectors');
  });

  it('falls back to Profiles for an unknown settings section', () => {
    expect(parseRoute('/settings/unknown', '').settingsSection).toBe('profiles');
  });
});
