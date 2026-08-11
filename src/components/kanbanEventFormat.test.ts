import { describe, expect, it } from 'vitest';
import type { KanbanTaskEvent } from '../types';
import { formatKanbanEvent } from './kanbanEventFormat';

function event(kind: string, payload: Record<string, unknown> | null): KanbanTaskEvent {
  return { id: 1, kind, payload, createdAt: '2026-07-27T14:06:00Z' };
}

describe('formatKanbanEvent', () => {
  it('turns a blocked payload into a readable reason and recurrence summary', () => {
    const result = formatKanbanEvent(event('blocked', {
      reason: 'Unable to verify the requested arithmetic because calculation was denied.',
      kind: 'needs_input',
      recurrences: 1,
    }));

    expect(result.title).toBe('Needs input');
    expect(result.description).toContain('Unable to verify');
    expect(result.details).toEqual(['Block type: Needs input', 'Occurrence 1']);
  });

  it('summarizes task creation without exposing the full skill list or null metadata', () => {
    const result = formatKanbanEvent(event('created', {
      assignee: 'f5r2wc',
      status: 'triage',
      parents: [],
      tenant: null,
      workspace_kind: 'dir',
      branch_name: null,
      project_id: null,
      skills: ['apple-notes', 'web-research', 'pdf'],
      goal_mode: null,
      model_override: null,
      provider_override: null,
    }), () => 'Research Agent');

    expect(result.description).toBe('Created in Backlog and assigned to Research Agent.');
    expect(result.details).toEqual(['Uses the selected project folder', '3 skills available']);
    expect(JSON.stringify(result)).not.toContain('apple-notes');
  });

  it('hides internal process details from worker lifecycle events', () => {
    const claimed = formatKanbanEvent(event('claimed', {
      lock: 'pop-os:2841840',
      expires: 1785136786,
      run_id: 9,
    }));
    const spawned = formatKanbanEvent(event('spawned', { pid: 2849902 }));
    const heartbeat = formatKanbanEvent(event('heartbeat', null));

    expect(claimed.description).toBe('Run 9 was claimed on pop-os.');
    expect(JSON.stringify(claimed)).not.toContain('2841840');
    expect(spawned.description).toBe('The worker process started successfully.');
    expect(JSON.stringify(spawned)).not.toContain('2849902');
    expect(heartbeat.description).toBe('The worker reported that it is still active.');
  });
});
