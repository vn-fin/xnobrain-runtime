import { useCallback, useEffect, useState } from 'react';
import { teamsApi, type Team, type TeamInput, type TeamRun, type TeamWorkflowStep } from '../api/teams';

export function useTeams() {
  const [teams, setTeams] = useState<Team[]>([]);
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [lastRun, setLastRun] = useState<TeamRun>();

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
      setLastRun((current) => current?.team_id === teamId ? undefined : current);
    } finally {
      setPending(false);
    }
  };

  const run = async (teamId: string, task: string, workflow: TeamWorkflowStep[] = []) => {
    setPending(true);
    try {
      const result = await teamsApi.run(teamId, task, workflow);
      setLastRun(result);
      setError('');
      return result;
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : 'Team run failed';
      setError(message);
      throw reason;
    } finally {
      setPending(false);
    }
  };

  return { teams, status, pending, error, lastRun, refresh, create, remove, run };
}
