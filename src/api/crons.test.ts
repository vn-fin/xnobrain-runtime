import { afterEach, describe, expect, it, vi } from 'vitest';
import { cronsApi } from './crons';

afterEach(() => vi.unstubAllGlobals());

describe('cronsApi', () => {
  it('creates through the Brain4All cron wrapper', async () => {
    const dto = { id: 'job-1', agent_id: 'agent-1', name: 'Health check', enabled: true, schedule: 'every 30m', prompt: 'Check the service', next_run_at: '2099-07-29T12:00:00Z' };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ success: true, data: dto }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);

    const result = await cronsApi.create({ agentId: 'agent-1', name: 'Health check', prompt: 'Check the service', intervalMinutes: 30 });

    expect(fetchMock.mock.calls[0][0]).toContain('/api/brain/v1/cron/jobs');
    expect(JSON.parse(fetchMock.mock.calls[0][1].body as string)).toEqual({ agent_id: 'agent-1', name: 'Health check', prompt: 'Check the service', interval_minutes: 30 });
    expect(result).toMatchObject({ agentId: 'agent-1', state: 'scheduled', intervalMinutes: 30 });
  });

  it('maps wrapper job detail and latest run', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ success: true, data: { job: { id: 'job-1', agent_id: 'agent-1', name: 'Digest', enabled: true, schedule: 'every 60m', prompt: 'Summarize' }, run: { id: 'run-1', state: 'success', triggered_at: '2026-07-29T12:00:00Z', completed_at: '2026-07-29T12:01:00Z', output: 'Done', error: '' } } }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);

    const detail = await cronsApi.detail('job-1');

    expect(detail.run).toMatchObject({ id: 'run-1', state: 'success', output: 'Done' });
    expect(fetchMock.mock.calls[0][0]).toContain('/api/brain/v1/cron/jobs/job-1');
  });

  it('maps the pending run returned by Run now', async () => {
    const dto = { job: { id: 'job-1', agent_id: 'agent-1', name: 'Digest', enabled: true, schedule: 'every 60m', prompt: 'Summarize' }, run: { id: 'pending-job-1', state: 'running', triggered_at: '2026-07-29T12:00:00Z' } };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ success: true, data: dto }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);

    const result = await cronsApi.runNow('job-1');

    expect(result.run).toMatchObject({ id: 'pending-job-1', state: 'running' });
    expect(fetchMock.mock.calls[0][0]).toContain('/api/brain/v1/cron/jobs/job-1/run');
  });

  it('maps blueprints and delivery targets', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ success: true, data: { blueprints: [{ key: 'brief', title: 'Brief', description: 'Daily brief', category: 'daily', tags: [], fields: [], schedule: '0 8 * * *', schedule_human: 'daily at 08:00' }] } }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ success: true, data: { options: [{ id: 'file', name: 'Workspace file', target_type: 'file', destination: '', available: true }] } }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);

    const blueprints = await cronsApi.listBlueprints();
    const options = await cronsApi.listDeliveryTargetOptions();

    expect(blueprints[0].scheduleHuman).toBe('daily at 08:00');
    expect(options[0]).toMatchObject({ targetType: 'file', name: 'Workspace file', available: true });
  });
});
