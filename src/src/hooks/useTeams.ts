import { useCallback, useEffect, useState } from 'react';
import {
  teamsApi,
  isRunTerminal,
  type Team,
  type TeamInput,
  type TeamRunRecord,
  type TeamWorkflowStep,
} from '../api/teams';

export function useTeams() {
  const [teams, setTeams] = useState<Team[]>([]);
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [runs, setRuns] = useState<TeamRunRecord[]>([]);
  const [activeRun, setActiveRun] = useState<TeamRunRecord>();
  const [runsStatus, setRunsStatus] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');

  const refresh = useCallback(async () => {
    setStatus('loading');
    try {
      setTeams(await teamsApi.list());
      setStatus('ready');
      setError('');
    } catch (reason) {
      setStatus('error');
      setError(reason instanceof Error ? reason.message : 'Could not load teams');
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

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

  const loadRuns = useCallback(async (teamId: string) => {
    if (!teamId) return;
    setRunsStatus('loading');
    try {
      setRuns(await teamsApi.listRuns(teamId));
      setRunsStatus('ready');
    } catch (reason) {
      setRunsStatus('error');
      setError(reason instanceof Error ? reason.message : 'Could not load runs');
    }
  }, []);

  const openRun = useCallback(async (teamId: string, runId: string) => {
    try {
      setActiveRun(await teamsApi.getRun(teamId, runId));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not load run');
    }
  }, []);

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
    refresh, create, remove, loadRuns, openRun, startRun, cancelRun, setActiveRun,
  };
}
