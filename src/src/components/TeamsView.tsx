import { useMemo, useState } from 'react';
import { Network, Play, Plus, Trash2, X } from 'lucide-react';
import type { TeamInput, TeamMember, TeamWorkflowStep } from '../api/teams';
import type { Agent } from '../types';
import type { useTeams } from '../hooks/useTeams';

type TeamsState = ReturnType<typeof useTeams>;

export function TeamsView({ agents, state, onClose }: { agents: Agent[]; state: TeamsState; onClose: () => void }) {
  const [name, setName] = useState('');
  const [orchestratorId, setOrchestratorId] = useState(agents[0]?.id ?? '');
  const [workerId, setWorkerId] = useState(agents.find((agent) => agent.id !== orchestratorId)?.id ?? '');
  const [role, setRole] = useState('researcher');
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [task, setTask] = useState('');
  const [workflowText, setWorkflowText] = useState('');
  const [workflowError, setWorkflowError] = useState('');
  const [selectedTeamId, setSelectedTeamId] = useState('');
  const availableWorkers = useMemo(
    () => agents.filter((agent) => agent.id !== orchestratorId && !members.some((member) => member.agent_id === agent.id)),
    [agents, members, orchestratorId],
  );

  const addMember = () => {
    if (!workerId || members.some((member) => member.agent_id === workerId)) return;
    const next = [...members, { agent_id: workerId, role: role.trim() || 'worker', allowed_tools: ['web'], enabled: true }];
    setMembers(next);
    setWorkerId(agents.find((agent) => agent.id !== orchestratorId && !next.some((member) => member.agent_id === agent.id))?.id ?? '');
  };

  const createTeam = async () => {
    const input: TeamInput = {
      name, orchestrator_id: orchestratorId,
      members: members.length ? members : [{ agent_id: workerId, role, allowed_tools: ['web'], enabled: true }],
      shared_workspace: false, max_parallel: Math.max(1, members.length || 1), max_depth: 1, enabled: true,
    };
    const created = await state.create(input);
    setSelectedTeamId(created.id);
    setName('');
    setMembers([]);
  };

  const runTeam = async () => {
    let workflow: TeamWorkflowStep[] = [];
    setWorkflowError('');
    if (workflowText.trim()) {
      try {
        const parsed = JSON.parse(workflowText) as unknown;
        if (!Array.isArray(parsed)) throw new Error('Workflow must be a JSON array.');
        workflow = parsed as TeamWorkflowStep[];
      } catch (error) {
        setWorkflowError(error instanceof Error ? error.message : 'Workflow JSON is invalid.');
        return;
      }
    }
    await state.run(selectedTeamId, task, workflow);
  };

  return (
    <section className="teams-view">
      <header className="teams-header">
        <div><span className="eyebrow">Hermes multi-agent workflows</span><h1><Network size={24} /> Agent teams</h1><p>Run specialized profiles in parallel or as a dependency-aware workflow, then synthesize their results.</p></div>
        <button className="icon-button" aria-label="Close teams" onClick={onClose}><X size={19} /></button>
      </header>
      {state.error && <div className="teams-error">{state.error}</div>}
      <div className="teams-grid">
        <div className="teams-card">
          <h2>Create team</h2>
          <label>Name<input value={name} onChange={(event) => setName(event.target.value)} placeholder="Research team" /></label>
          <label>Orchestrator<select value={orchestratorId} onChange={(event) => { setOrchestratorId(event.target.value); setMembers([]); setWorkerId(agents.find((agent) => agent.id !== event.target.value)?.id ?? ''); }}>{agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.title}</option>)}</select></label>
          <label>Worker<select value={workerId} onChange={(event) => setWorkerId(event.target.value)}>{availableWorkers.map((agent) => <option key={agent.id} value={agent.id}>{agent.title}</option>)}</select></label>
          <label>Worker role<input value={role} onChange={(event) => setRole(event.target.value)} /></label>
          <button className="conn-btn ghost" disabled={!workerId} onClick={addMember}><Plus size={15} /> Add worker</button>
          {members.map((member) => <div className="team-policy" key={member.agent_id}>{agents.find((agent) => agent.id === member.agent_id)?.title ?? member.agent_id} · {member.role}<button className="icon-button" onClick={() => setMembers((current) => current.filter((item) => item.agent_id !== member.agent_id))}><X size={13} /></button></div>)}
          <div className="team-policy">Toolset: web · Parallel: {Math.max(1, members.length)} · Shared memory writes: blocked</div>
          <button className="primary-button" disabled={state.pending || !name.trim() || !orchestratorId || (members.length === 0 && !workerId)} onClick={() => void createTeam()}><Plus size={16} /> Create</button>
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
          {selectedTeamId && <div className="team-run"><label>Task<textarea value={task} onChange={(event) => setTask(event.target.value)} placeholder="Investigate and summarize…" /></label><label>Workflow DAG <small>optional JSON</small><textarea value={workflowText} onChange={(event) => setWorkflowText(event.target.value)} placeholder={'[{"id":"research","task":"Research the API","role":"researcher"},{"id":"review","task":"Review findings","role":"reviewer","needs":["research"]}]'} /></label>{workflowError && <p className="teams-error">{workflowError}</p>}<button className="primary-button" disabled={state.pending || (!task.trim() && !workflowText.trim())} onClick={() => void runTeam()}><Play size={16} /> {state.pending ? 'Running…' : 'Run workflow'}</button></div>}
        </div>
      </div>
      {state.lastRun && <div className="teams-card team-result"><h2>Final orchestrator summary</h2><p>{state.lastRun.orchestrator_summary}</p>{state.lastRun.member_results.map((result) => <details key={result.id}><summary>{result.id} · {result.role} · {result.status}</summary><p>{result.summary || result.error}</p></details>)}</div>}
    </section>
  );
}
