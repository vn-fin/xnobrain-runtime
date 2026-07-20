import { useMemo, useState } from 'react';
import { Network, Play, Plus, Trash2, X } from 'lucide-react';
import type { TeamInput } from '../api/teams';
import type { Agent } from '../types';
import type { useTeams } from '../hooks/useTeams';

type TeamsState = ReturnType<typeof useTeams>;

export function TeamsView({ agents, state, onClose }: { agents: Agent[]; state: TeamsState; onClose: () => void }) {
  const [name, setName] = useState('');
  const [orchestratorId, setOrchestratorId] = useState(agents[0]?.id ?? '');
  const [workerId, setWorkerId] = useState(agents.find((agent) => agent.id !== orchestratorId)?.id ?? '');
  const [role, setRole] = useState('researcher');
  const [task, setTask] = useState('');
  const [selectedTeamId, setSelectedTeamId] = useState('');
  const availableWorkers = useMemo(() => agents.filter((agent) => agent.id !== orchestratorId), [agents, orchestratorId]);

  const createTeam = async () => {
    const input: TeamInput = {
      name, orchestrator_id: orchestratorId,
      members: [{ agent_id: workerId, role, allowed_tools: ['web'], enabled: true }],
      shared_workspace: false, max_parallel: 1, max_depth: 1, enabled: true,
    };
    const created = await state.create(input);
    setSelectedTeamId(created.id);
    setName('');
  };

  return (
    <section className="teams-view">
      <header className="teams-header">
        <div><span className="eyebrow">Hermes delegation</span><h1><Network size={24} /> Agent teams</h1><p>One orchestrator and one bounded delegated worker are available in the Free plan.</p></div>
        <button className="icon-button" aria-label="Close teams" onClick={onClose}><X size={19} /></button>
      </header>
      {state.error && <div className="teams-error">{state.error}</div>}
      <div className="teams-grid">
        <div className="teams-card">
          <h2>Create team</h2>
          <label>Name<input value={name} onChange={(event) => setName(event.target.value)} placeholder="Research team" /></label>
          <label>Orchestrator<select value={orchestratorId} onChange={(event) => { setOrchestratorId(event.target.value); setWorkerId(agents.find((agent) => agent.id !== event.target.value)?.id ?? ''); }}>{agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select></label>
          <label>Worker<select value={workerId} onChange={(event) => setWorkerId(event.target.value)}>{availableWorkers.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select></label>
          <label>Worker role<input value={role} onChange={(event) => setRole(event.target.value)} /></label>
          <div className="team-policy">Toolset: web · Parallel: 1 · Depth: 1 · Shared memory writes: blocked</div>
          <button className="primary-button" disabled={state.pending || !name.trim() || !orchestratorId || !workerId} onClick={() => void createTeam()}><Plus size={16} /> Create</button>
        </div>
        <div className="teams-card team-list-card">
          <h2>Saved teams <span>{state.teams.length}/1</span></h2>
          {state.status === 'loading' && <p>Loading teams…</p>}
          {state.teams.length === 0 && state.status !== 'loading' && <p>No team configured yet.</p>}
          {state.teams.map((team) => (
            <button key={team.id} className={selectedTeamId === team.id ? 'team-row active' : 'team-row'} onClick={() => setSelectedTeamId(team.id)}>
              <span><strong>{team.name}</strong><small>{team.members.length + 1} agents · {team.enabled ? 'Enabled' : 'Paused'}</small></span>
              <Trash2 size={15} onClick={(event) => { event.stopPropagation(); void state.remove(team.id); }} />
            </button>
          ))}
          {selectedTeamId && <div className="team-run"><label>Task<textarea value={task} onChange={(event) => setTask(event.target.value)} placeholder="Investigate and summarize…" /></label><button className="primary-button" disabled={state.pending || !task.trim()} onClick={() => void state.run(selectedTeamId, task)}><Play size={16} /> {state.pending ? 'Running…' : 'Run team'}</button></div>}
        </div>
      </div>
      {state.lastRun && <div className="teams-card team-result"><h2>Final orchestrator summary</h2><p>{state.lastRun.orchestrator_summary}</p>{state.lastRun.member_results.map((result) => <details key={result.agent_id}><summary>{result.role}</summary><p>{result.summary || result.error}</p></details>)}</div>}
    </section>
  );
}
