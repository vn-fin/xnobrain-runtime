import { useEffect, useMemo, useState } from 'react';
import { Network, Play, Plus, Square, Trash2, X } from 'lucide-react';
import { isRunTerminal, type TeamInput, type TeamMember, type TeamRunRecord, type TeamRunStep, type TeamWorkflowStep } from '../api/teams';
import type { Agent } from '../types';
import type { useTeams } from '../hooks/useTeams';

type TeamsState = ReturnType<typeof useTeams>;

function StepChip({ step }: { step: TeamRunStep }) {
  return (
    <span className={`run-chip ${step.status}`} title={step.error ?? step.summary ?? ''}>
      {step.id} · {step.role} · {step.status}
    </span>
  );
}

function TeamRunsPanel({ teamId, state }: { teamId: string; state: TeamsState }) {
  const { runs, activeRun, runsStatus, openRun, cancelRun } = state;
  const canCancel = activeRun && activeRun.team_id === teamId && !isRunTerminal(activeRun.status);

  return (
    <div className="teams-card team-runs">
      <h2>Runs <span>{runs.length}</span></h2>
      {runsStatus === 'loading' && <p>Loading runs…</p>}
      {runsStatus !== 'loading' && runs.length === 0 && <p>No runs yet. Start one above.</p>}
      <div className="run-history">
        {runs.map((run: TeamRunRecord) => (
          <button
            key={run.id}
            className={activeRun?.id === run.id ? 'run-row active' : 'run-row'}
            onClick={() => void openRun(teamId, run.id)}
          >
            <span className="run-id">{run.id.replace(/^tr_/, '').slice(0, 8)}</span>
            <span className={`run-chip ${run.status}`}>{run.status}</span>
            <small>{run.mode} · {run.steps.length} steps · {run.started_at ?? run.created_at}</small>
          </button>
        ))}
      </div>
      {activeRun && activeRun.team_id === teamId && (
        <div className="run-detail">
          <div className="run-detail-head">
            <strong>{activeRun.id.replace(/^tr_/, '').slice(0, 8)}</strong>
            <span className={`run-chip ${activeRun.status}`}>{activeRun.status}</span>
            {canCancel && (
              <button className="conn-btn ghost" onClick={() => void cancelRun(teamId, activeRun.id)}>
                <Square size={13} /> Cancel
              </button>
            )}
          </div>
          <div className="run-chips">
            {activeRun.steps.map((step) => <StepChip key={step.id} step={step} />)}
          </div>
          {activeRun.error && <p className="teams-error">Run failed: {activeRun.error}</p>}
          {isRunTerminal(activeRun.status) && activeRun.orchestrator_summary && (
            <div className="team-result"><h3>Orchestrator summary</h3><p>{activeRun.orchestrator_summary}</p></div>
          )}
          {activeRun.steps.map((step) => (
            <details key={step.id}>
              <summary>{step.id} · {step.role} · {step.status}</summary>
              <p>{step.summary || step.error || '…'}</p>
            </details>
          ))}
        </div>
      )}
    </div>
  );
}

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

  const { loadRuns } = state;
  useEffect(() => {
    if (selectedTeamId) void loadRuns(selectedTeamId);
  }, [selectedTeamId, loadRuns]);

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
    await state.startRun(selectedTeamId, task, workflow);
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
          {selectedTeamId && <div className="team-run"><label>Task<textarea value={task} onChange={(event) => setTask(event.target.value)} placeholder="Investigate and summarize…" /></label><label>Workflow DAG <small>optional JSON</small><textarea value={workflowText} onChange={(event) => setWorkflowText(event.target.value)} placeholder={'[{"id":"research","task":"Research the API","role":"researcher"},{"id":"review","task":"Review findings","role":"reviewer","needs":["research"]}]'} /></label>{workflowError && <p className="teams-error">{workflowError}</p>}<button className="primary-button" disabled={state.pending || (!task.trim() && !workflowText.trim())} onClick={() => void runTeam()}><Play size={16} /> {state.pending ? 'Starting…' : 'Run (async)'}</button></div>}
        </div>
      </div>
      {selectedTeamId && <TeamRunsPanel teamId={selectedTeamId} state={state} />}
    </section>
  );
}
