import { useCallback, useEffect, useRef, useState } from 'react';
import {
  teamsApi,
  isRunTerminal,
  type Team,
  type TeamInput,
  type TeamRunRecord,
  type TeamWorkflowStep,
} from '../api/teams';

export function useTeams(active = true) {
  const [teams, setTeams] = useState<Team[]>([]);
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [runs, setRuns] = useState<TeamRunRecord[]>([]);
  const [activeRun, setActiveRun] = useState<TeamRunRecord>();
  const [runsStatus, setRunsStatus] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const teamsRequest = useRef<Promise<void> | null>(null);
  const runsRequests = useRef(new Map<string, Promise<void>>());

  const refresh = useCallback(() => {
    if (teamsRequest.current) return teamsRequest.current;
    const request = (async () => {
      setStatus('loading');
      try {
        setTeams(await teamsApi.list());
        setStatus('ready');
        setError('');
      } catch (reason) {
        setStatus('error');
        setError(reason instanceof Error ? reason.message : 'Could not load teams');
      }
    })().finally(() => {
      if (teamsRequest.current === request) teamsRequest.current = null;
    });
    teamsRequest.current = request;
    return request;
  }, []);

  useEffect(() => { if (active) void refresh(); }, [active, refresh]);

  const create = async (input: TeamInput) => {
    setPending(true);
    try {
      const team = await teamsApi.create(input);
      setTeams((current) => [...current, team]);
      setError('');
      return team;
    } finally {
      setPending(false);
    }
  };

  const rename = async (teamId: string, name: string) => {
    const current = teams.find((team) => team.id === teamId);
    const nextName = name.trim();
    if (!current || !nextName || current.name === nextName) return current;
    setPending(true);
    try {
      const updated = await teamsApi.update({ ...current, name: nextName });
      setTeams((rows) => rows.map((team) => team.id === teamId ? updated : team));
      setError('');
      return updated;
    } finally {
      setPending(false);
    }
  };

  const update = async (input: Team) => {
    setPending(true);
    try {
      const updated = await teamsApi.update(input);
      setTeams((rows) => rows.map((team) => team.id === input.id ? updated : team));
      setError('');
      return updated;
    } finally {
      setPending(false);
    }
  };

  const remove = async (teamId: string) => {
    setPending(true);
    try {
      await teamsApi.remove(teamId);
      setTeams((current) => current.filter((team) => team.id !== teamId));
      setActiveRun((current) => (current?.team_id === teamId ? undefined : current));
      setRuns((current) => current.filter((run) => run.team_id !== teamId));
    } finally {
      setPending(false);
    }
  };

  const loadRuns = useCallback((teamId: string) => {
    if (!teamId) return;
    const pendingRequest = runsRequests.current.get(teamId);
    if (pendingRequest) return pendingRequest;

    const request = (async () => {
      setRunsStatus('loading');
      try {
        const rows = await teamsApi.listRuns(teamId);
        setRuns(rows);
        const live = rows.find((run) => !isRunTerminal(run.status));
        if (live) {
          setActiveRun(await teamsApi.getRun(teamId, live.id));
        } else {
          setActiveRun((current) => current?.team_id === teamId ? current : undefined);
        }
        setRunsStatus('ready');
      } catch (reason) {
        setRunsStatus('error');
        setError(reason instanceof Error ? reason.message : 'Could not load runs');
      }
    })().finally(() => {
      if (runsRequests.current.get(teamId) === request) runsRequests.current.delete(teamId);
    });
    runsRequests.current.set(teamId, request);
    return request;
  }, []);

  const openRun = useCallback(async (teamId: string, runId: string) => {
    try {
      setActiveRun(await teamsApi.getRun(teamId, runId));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not load run');
    }
  }, []);

  const deleteRun = async (teamId: string, runId: string) => {
    setPending(true);
    try {
      await teamsApi.deleteRun(teamId, runId);
      setRuns((current) => current.filter((run) => run.id !== runId));
      setActiveRun((current) => current?.id === runId ? undefined : current);
      setError('');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not delete execution');
      throw reason;
    } finally {
      setPending(false);
    }
  };

  const startRun = async (teamId: string, task: string, workflow: TeamWorkflowStep[] = [], synthesis?: string) => {
    setPending(true);
    try {
      const record = await teamsApi.startRun(teamId, task, workflow, synthesis);
      setActiveRun(record);
      setRuns((current) => [record, ...current.filter((run) => run.id !== record.id)]);
      setError('');
      return record;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Team run failed');
      throw reason;
    } finally {
      setPending(false);
    }
  };

  const applyRun = useCallback((record: TeamRunRecord) => {
    setActiveRun((current) => (current && current.id === record.id ? record : current));
    setRuns((current) => current.map((run) => (run.id === record.id ? record : run)));
  }, []);

  const cancelRun = async (teamId: string, runId: string) => {
    try {
      const record = await teamsApi.cancelRun(teamId, runId);
      applyRun(record);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not cancel run');
    }
  };

  // Follow the active run over SSE while it is not terminal, replacing the run
  // state on every transition and closing the stream once it finishes. Keyed on
  // primitives (id + terminal flag) so mid-run updates do not reconnect the stream.
  const watchTeamId = activeRun?.team_id;
  const watchRunId = activeRun?.id;
  const watchTerminal = activeRun ? isRunTerminal(activeRun.status) : true;
  useEffect(() => {
    if (!watchTeamId || !watchRunId || watchTerminal) return undefined;
    const controller = new AbortController();

    const connect = async () => {
      try {
        await teamsApi.watchRun(watchTeamId, watchRunId, (event) => {
          if (event.event === 'run' && event.data && typeof event.data === 'object') {
            applyRun(event.data as TeamRunRecord);
          }
        }, controller.signal);
      } catch {
        // aborted or dropped — the terminal record is still readable via getRun.
      }
    };

    void connect();
    return () => controller.abort();
  }, [watchTeamId, watchRunId, watchTerminal, applyRun]);

  return {
    teams, status, pending, error, runs, activeRun, runsStatus,
    refresh, create, update, rename, remove, loadRuns, openRun, deleteRun, startRun, cancelRun, setActiveRun,
  };
}
