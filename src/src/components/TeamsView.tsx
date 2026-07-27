import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import {
  ArrowLeft,
  Bot,
  Check,
  ChevronRight,
  GitBranch,
  List,
  MousePointer2,
  Network,
  Play,
  Plus,
  Save,
  Square,
  Trash2,
  X,
} from 'lucide-react';
import {
  isRunTerminal,
  type Team,
  type TeamInput,
  type TeamRunRecord,
  type TeamRunStep,
  type TeamWorkflowStep,
} from '../api/teams';
import type { Agent } from '../types';
import type { useTeams } from '../hooks/useTeams';

type TeamsState = ReturnType<typeof useTeams>;
type TeamsMode = 'library' | 'builder';
type DraftNode = TeamWorkflowStep & { agent_id: string; role: string; x: number; y: number };

const CANVAS_WIDTH = 900;
const CANVAS_HEIGHT = 620;
const NODE_WIDTH = 220;
const NODE_HEIGHT = 118;

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
      <div className="teams-section-heading">
        <div>
          <span className="teams-kicker">Execution history</span>
          <h2>Recent runs</h2>
        </div>
        <span className="teams-count">{runs.length}</span>
      </div>
      {runsStatus === 'loading' && <p className="teams-empty-copy">Loading runs…</p>}
      {runsStatus !== 'loading' && runs.length === 0 && <p className="teams-empty-copy">No runs yet. Trigger this team to see live progress here.</p>}
      <div className="run-history">
        {runs.map((run: TeamRunRecord) => (
          <button
            key={run.id}
            className={activeRun?.id === run.id ? 'run-row active' : 'run-row'}
            onClick={() => void openRun(teamId, run.id)}
          >
            <span className="run-id">{run.id.replace(/^tr_/, '').slice(0, 8)}</span>
            <span className={`run-chip ${run.status}`}>{run.status}</span>
            <small>{run.steps.length} stages · {run.started_at ?? run.created_at}</small>
          </button>
        ))}
      </div>
      {activeRun && activeRun.team_id === teamId && (
        <div className="run-detail">
          <div className="run-detail-head">
            <strong>Run {activeRun.id.replace(/^tr_/, '').slice(0, 8)}</strong>
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
            <div className="team-result"><h3>Coordinator summary</h3><p>{activeRun.orchestrator_summary}</p></div>
          )}
          {activeRun.steps.map((step) => (
            <details key={step.id}>
              <summary>{step.id} · {step.role} · {step.status}</summary>
              <p>{step.summary || step.error || 'Waiting for output…'}</p>
            </details>
          ))}
        </div>
      )}
    </div>
  );
}

function safeId(value: string, fallback: string) {
  const normalized = value.toLowerCase().replace(/[^a-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '');
  return normalized || fallback;
}

function createsCycle(nodes: DraftNode[], from: string, to: string) {
  const outgoing = new Map<string, string[]>();
  nodes.forEach((node) => node.needs?.forEach((need) => {
    outgoing.set(need, [...(outgoing.get(need) ?? []), node.id]);
  }));
  const stack = [to];
  const visited = new Set<string>();
  while (stack.length) {
    const current = stack.pop() as string;
    if (current === from) return true;
    if (visited.has(current)) continue;
    visited.add(current);
    stack.push(...(outgoing.get(current) ?? []));
  }
  return false;
}

function curve(fromX: number, fromY: number, toX: number, toY: number) {
  const bend = Math.max(50, Math.abs(toX - fromX) * 0.45);
  return `M ${fromX} ${fromY} C ${fromX + bend} ${fromY}, ${toX - bend} ${toY}, ${toX} ${toY}`;
}

function WorkflowEdges({ nodes }: { nodes: DraftNode[] }) {
  const ids = new Set(nodes.map((node) => node.id));
  const roots = nodes.filter((node) => !node.needs?.some((need) => ids.has(need)));
  const used = new Set(nodes.flatMap((node) => node.needs ?? []));
  const leaves = nodes.filter((node) => !used.has(node.id));
  return (
    <svg className="team-canvas-edges" viewBox={`0 0 ${CANVAS_WIDTH} ${CANVAS_HEIGHT}`} aria-hidden="true">
      <defs>
        <marker id="team-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" />
        </marker>
      </defs>
      {roots.map((node) => (
        <path key={`start-${node.id}`} className="team-edge virtual" d={curve(116, 310, node.x, node.y + NODE_HEIGHT / 2)} />
      ))}
      {nodes.flatMap((node) => (node.needs ?? []).map((need) => {
        const parent = nodes.find((candidate) => candidate.id === need);
        if (!parent) return null;
        return (
          <path
            key={`${need}-${node.id}`}
            className="team-edge"
            d={curve(parent.x + NODE_WIDTH, parent.y + NODE_HEIGHT / 2, node.x, node.y + NODE_HEIGHT / 2)}
          />
        );
      }))}
      {leaves.map((node) => (
        <path key={`${node.id}-finish`} className="team-edge virtual" d={curve(node.x + NODE_WIDTH, node.y + NODE_HEIGHT / 2, 796, 310)} />
      ))}
    </svg>
  );
}

function TeamPreview({ team, agents }: { team: Team; agents: Agent[] }) {
  const workflow = team.workflow?.length
    ? team.workflow
    : team.members.map((member, index) => ({
      id: `worker-${index + 1}`,
      task: '',
      agent_id: member.agent_id,
      role: member.role,
      needs: [],
    }));
  return (
    <div className="team-preview" aria-label={`${team.name} workflow`}>
      <div className="team-preview-terminal start"><Play size={12} fill="currentColor" /> Start</div>
      <ChevronRight size={16} />
      <div className="team-preview-stages">
        {workflow.map((step) => (
          <div className="team-preview-node" key={step.id}>
            <Bot size={15} />
            <span>
              <strong>{agents.find((agent) => agent.id === step.agent_id)?.title ?? step.role ?? step.id}</strong>
              <small>{step.role ?? step.id}{step.needs?.length ? ` · after ${step.needs.join(', ')}` : ''}</small>
            </span>
          </div>
        ))}
      </div>
      <ChevronRight size={16} />
      <div className="team-preview-terminal finish"><Check size={13} /> Finish</div>
    </div>
  );
}

function TeamBuilder({
  agents,
  state,
  onSaved,
  onBack,
}: {
  agents: Agent[];
  state: TeamsState;
  onSaved: (team: Team) => void;
  onBack: () => void;
}) {
  const [name, setName] = useState('');
  const [orchestratorId, setOrchestratorId] = useState(agents[0]?.id ?? '');
  const [nodes, setNodes] = useState<DraftNode[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState('');
  const [connectFrom, setConnectFrom] = useState('');
  const [message, setMessage] = useState('');
  const [drag, setDrag] = useState<{ id: string; pointerId: number; dx: number; dy: number }>();
  const canvasRef = useRef<HTMLDivElement>(null);
  const selectedNode = nodes.find((node) => node.id === selectedNodeId);
  const availableAgents = agents.filter((agent) => agent.id !== orchestratorId && !nodes.some((node) => node.agent_id === agent.id));

  useEffect(() => {
    if (!agents.some((agent) => agent.id === orchestratorId)) setOrchestratorId(agents[0]?.id ?? '');
  }, [agents, orchestratorId]);

  const addAgent = (agent: Agent) => {
    const role = safeId(agent.title, `worker-${nodes.length + 1}`);
    const baseId = safeId(role, `step-${nodes.length + 1}`);
    let id = baseId;
    let suffix = 2;
    while (nodes.some((node) => node.id === id)) id = `${baseId}-${suffix++}`;
    const index = nodes.length;
    const next: DraftNode = {
      id,
      agent_id: agent.id,
      role,
      task: `Complete the ${role.replace(/-/g, ' ')} stage for the team objective.`,
      needs: [],
      allowed_tools: ['web'],
      x: 170 + (index % 2) * 260,
      y: 95 + Math.floor(index / 2) * 165,
    };
    setNodes((current) => [...current, next]);
    setSelectedNodeId(id);
    setMessage('');
  };

  const updateNode = (id: string, patch: Partial<DraftNode>) => {
    setNodes((current) => current.map((node) => node.id === id ? { ...node, ...patch } : node));
  };

  const removeNode = (id: string) => {
    setNodes((current) => current
      .filter((node) => node.id !== id)
      .map((node) => ({ ...node, needs: (node.needs ?? []).filter((need) => need !== id) })));
    setSelectedNodeId((current) => current === id ? '' : current);
    setConnectFrom((current) => current === id ? '' : current);
  };

  const connect = (to: string) => {
    if (!connectFrom || connectFrom === to) return;
    if (createsCycle(nodes, connectFrom, to)) {
      setMessage('That connection would create a cycle. DAG workflows must always move forward.');
      setConnectFrom('');
      return;
    }
    setNodes((current) => current.map((node) => node.id === to
      ? { ...node, needs: [...new Set([...(node.needs ?? []), connectFrom])] }
      : node));
    setConnectFrom('');
    setMessage('');
  };

  const beginDrag = (event: ReactPointerEvent<HTMLDivElement>, node: DraftNode) => {
    if ((event.target as HTMLElement).closest('button, input, textarea')) return;
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;
    const scaleX = CANVAS_WIDTH / rect.width;
    const scaleY = CANVAS_HEIGHT / rect.height;
    setDrag({
      id: node.id,
      pointerId: event.pointerId,
      dx: (event.clientX - rect.left) * scaleX - node.x,
      dy: (event.clientY - rect.top) * scaleY - node.y,
    });
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const moveDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!drag || event.pointerId !== drag.pointerId) return;
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;
    const x = (event.clientX - rect.left) * (CANVAS_WIDTH / rect.width) - drag.dx;
    const y = (event.clientY - rect.top) * (CANVAS_HEIGHT / rect.height) - drag.dy;
    updateNode(drag.id, {
      x: Math.max(130, Math.min(540, x)),
      y: Math.max(28, Math.min(CANVAS_HEIGHT - NODE_HEIGHT - 28, y)),
    });
  };

  const save = async () => {
    setMessage('');
    if (!name.trim() || !orchestratorId || nodes.length === 0) {
      setMessage('Add a team name, coordinator, and at least one agent.');
      return;
    }
    if (nodes.some((node) => !node.task.trim() || !node.role.trim())) {
      setMessage('Every agent needs a role and stage instruction.');
      return;
    }
    const input: TeamInput = {
      name: name.trim(),
      orchestrator_id: orchestratorId,
      members: nodes.map((node) => ({
        agent_id: node.agent_id,
        role: node.role.trim(),
        allowed_tools: ['web'],
        enabled: true,
      })),
      workflow: nodes.map(({ x: _x, y: _y, ...node }) => ({
        ...node,
        role: node.role.trim(),
        task: node.task.trim(),
        needs: node.needs ?? [],
        allowed_tools: ['web'],
      })),
      shared_workspace: false,
      max_parallel: Math.max(1, nodes.length),
      max_depth: 1,
      enabled: true,
    };
    try {
      onSaved(await state.create(input));
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : 'Could not save the team.');
    }
  };

  return (
    <div className={`team-builder-shell ${selectedNode ? 'has-inspector' : ''}`}>
      <aside className="team-builder-sidebar">
        <button className="teams-back-button" onClick={onBack}>
          <ArrowLeft size={15} /> Team library
        </button>
        <div className="team-builder-form">
          <span className="teams-kicker">Team setup</span>
          <label>
            Team name
            <input value={name} onChange={(event) => setName(event.target.value)} placeholder="Product launch team" />
          </label>
          <label>
            Coordinator
            <select
              value={orchestratorId}
              onChange={(event) => {
                setOrchestratorId(event.target.value);
                setNodes((current) => current.filter((node) => node.agent_id !== event.target.value));
              }}
            >
              {agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.title}</option>)}
            </select>
          </label>
        </div>
        <div className="team-agent-palette">
          <div className="team-palette-heading">
            <span>Available agents</span>
            <small>{availableAgents.length}</small>
          </div>
          {availableAgents.map((agent) => (
            <button key={agent.id} className="team-agent-option" onClick={() => addAgent(agent)}>
              <span className="team-agent-avatar">{agent.title.slice(0, 1).toUpperCase()}</span>
              <span><strong>{agent.title}</strong><small>{agent.description || agent.model}</small></span>
              <Plus size={15} />
            </button>
          ))}
          {availableAgents.length === 0 && <p className="teams-empty-copy">All available profiles are on the canvas.</p>}
        </div>
        <div className="team-safety-note">
          <Check size={14} />
          <span><strong>Safe delegation</strong><small>Web-only workers · no shared memory writes · flat depth</small></span>
        </div>
      </aside>

      <main className="team-builder-main">
        <div className="team-builder-toolbar">
          <div>
            <span className="teams-kicker">Visual workflow</span>
            <h2>{name.trim() || 'Untitled team'}</h2>
          </div>
          <div className="team-toolbar-help"><MousePointer2 size={14} /> Drag cards to arrange · click ports to connect</div>
          <button className="primary-button" disabled={state.pending} onClick={() => void save()}>
            <Save size={15} /> {state.pending ? 'Saving…' : 'Save team'}
          </button>
        </div>
        {message && <div className="teams-inline-message" role="alert">{message}</div>}
        <div className="team-canvas-wrap">
          <div
            ref={canvasRef}
            className={`team-canvas ${connectFrom ? 'is-connecting' : ''}`}
            onPointerMove={moveDrag}
            onPointerUp={() => setDrag(undefined)}
            onPointerCancel={() => setDrag(undefined)}
          >
            <div className="team-canvas-grid" />
            <WorkflowEdges nodes={nodes} />
            <div className="team-terminal-node start" style={{ left: 32, top: 270 }}>
              <span><Play size={14} fill="currentColor" /></span><strong>Start</strong><small>Team objective</small>
            </div>
            {nodes.map((node) => {
              const agent = agents.find((candidate) => candidate.id === node.agent_id);
              return (
                <div
                  key={node.id}
                  className={`team-workflow-node ${selectedNodeId === node.id ? 'selected' : ''}`}
                  style={{ left: node.x, top: node.y }}
                  onPointerDown={(event) => beginDrag(event, node)}
                  onClick={() => setSelectedNodeId(node.id)}
                >
                  <button
                    className="team-node-port input"
                    aria-label={`Connect to ${node.id}`}
                    title="Connect into this stage"
                    onClick={(event) => { event.stopPropagation(); connect(node.id); }}
                  />
                  <div className="team-node-head">
                    <span className="team-agent-avatar">{agent?.title.slice(0, 1).toUpperCase() ?? 'A'}</span>
                    <span><strong>{agent?.title ?? node.agent_id}</strong><small>{node.role}</small></span>
                    <button className="team-node-remove" aria-label={`Remove ${node.id}`} onClick={(event) => { event.stopPropagation(); removeNode(node.id); }}><X size={13} /></button>
                  </div>
                  <p>{node.task}</p>
                  <div className="team-node-foot"><span>WEB</span><span>{node.needs?.length ?? 0} input{node.needs?.length === 1 ? '' : 's'}</span></div>
                  <button
                    className={`team-node-port output ${connectFrom === node.id ? 'active' : ''}`}
                    aria-label={`Connect from ${node.id}`}
                    title="Start a connection"
                    onClick={(event) => {
                      event.stopPropagation();
                      setConnectFrom((current) => current === node.id ? '' : node.id);
                      setMessage('');
                    }}
                  />
                </div>
              );
            })}
            <div className="team-terminal-node finish" style={{ left: 794, top: 270 }}>
              <span><Check size={15} /></span><strong>Finish</strong><small>Synthesize</small>
            </div>
            {nodes.length === 0 && (
              <div className="team-canvas-empty">
                <div><Plus size={22} /></div>
                <strong>Add your first agent</strong>
                <span>Choose a profile from the left. Then add more agents and connect their ports.</span>
              </div>
            )}
          </div>
        </div>
      </main>

      <aside className={`team-node-inspector ${selectedNode ? 'open' : ''}`}>
        {selectedNode ? (
          <>
            <div className="team-inspector-head">
              <div><span className="teams-kicker">Stage settings</span><h3>{selectedNode.id}</h3></div>
              <button className="icon-button" aria-label="Close stage settings" onClick={() => setSelectedNodeId('')}><X size={16} /></button>
            </div>
            <label>
              Role
              <input value={selectedNode.role} onChange={(event) => updateNode(selectedNode.id, { role: event.target.value })} />
            </label>
            <label>
              Instructions
              <textarea value={selectedNode.task} onChange={(event) => updateNode(selectedNode.id, { task: event.target.value })} />
            </label>
            <label>
              Agent
              <input value={agents.find((agent) => agent.id === selectedNode.agent_id)?.title ?? selectedNode.agent_id} disabled />
            </label>
            <div className="team-inspector-section">
              <span>Depends on</span>
              {(selectedNode.needs ?? []).length === 0 && <small>No dependencies — starts immediately</small>}
              {(selectedNode.needs ?? []).map((need) => (
                <button key={need} onClick={() => updateNode(selectedNode.id, { needs: selectedNode.needs?.filter((id) => id !== need) })}>
                  {need}<X size={12} />
                </button>
              ))}
            </div>
          </>
        ) : (
          <div className="team-inspector-empty"><MousePointer2 size={20} /><span>Select a stage to edit its role and instructions.</span></div>
        )}
      </aside>
    </div>
  );
}

function TeamLibrary({
  agents,
  state,
  selectedTeamId,
  setSelectedTeamId,
  onCreate,
}: {
  agents: Agent[];
  state: TeamsState;
  selectedTeamId: string;
  setSelectedTeamId: (id: string) => void;
  onCreate: () => void;
}) {
  const [task, setTask] = useState('');
  const selectedTeam = state.teams.find((team) => team.id === selectedTeamId);
  const { loadRuns } = state;

  useEffect(() => {
    if (!selectedTeamId && state.teams[0]) setSelectedTeamId(state.teams[0].id);
  }, [selectedTeamId, setSelectedTeamId, state.teams]);

  useEffect(() => {
    if (selectedTeamId) void loadRuns(selectedTeamId);
  }, [selectedTeamId, loadRuns]);

  const run = async () => {
    if (!selectedTeam || !task.trim()) return;
    const workflow = (selectedTeam.workflow ?? []).map((step) => ({
      ...step,
      task: `${step.task}\n\nTeam objective: ${task.trim()}`,
    }));
    try {
      await state.startRun(selectedTeam.id, task.trim(), workflow);
    } catch {
      // The shared teams banner reports the API failure.
    }
  };

  return (
    <div className="team-library-layout">
      <aside className="team-library-list">
        <div className="teams-section-heading">
          <div><span className="teams-kicker">Your workspace</span><h2>Saved teams</h2></div>
          <span className="teams-count">{state.teams.length}</span>
        </div>
        <button className="team-new-card" onClick={onCreate}><span><Plus size={17} /></span><div><strong>Create a new team</strong><small>Design a visual workflow</small></div></button>
        {state.status === 'loading' && <p className="teams-empty-copy">Loading teams…</p>}
        {state.teams.map((team) => (
          <div className={`team-list-item ${selectedTeamId === team.id ? 'active' : ''}`} key={team.id}>
            <button className="team-list-select" onClick={() => setSelectedTeamId(team.id)}>
              <span className="team-list-icon"><GitBranch size={17} /></span>
              <span><strong>{team.name}</strong><small>{team.members.length + 1} agents · {team.workflow?.length || team.members.length} stages</small></span>
              <ChevronRight size={15} />
            </button>
            <button className="team-list-delete" aria-label={`Delete ${team.name}`} onClick={() => void state.remove(team.id)}><Trash2 size={14} /></button>
          </div>
        ))}
        {state.status !== 'loading' && state.teams.length === 0 && (
          <div className="team-library-empty"><Network size={23} /><strong>No teams yet</strong><span>Create a workflow to get started.</span></div>
        )}
      </aside>

      <main className="team-library-detail">
        {selectedTeam ? (
          <>
            <div className="team-detail-hero">
              <div className="team-detail-title">
                <span className="team-detail-icon"><Network size={22} /></span>
                <div><span className="teams-kicker">Agent team</span><h2>{selectedTeam.name}</h2><p>{selectedTeam.members.length + 1} profiles collaborate through {selectedTeam.workflow?.length || selectedTeam.members.length} workflow stages.</p></div>
              </div>
              <span className={`team-status ${selectedTeam.enabled ? 'enabled' : ''}`}><i />{selectedTeam.enabled ? 'Ready' : 'Paused'}</span>
            </div>
            <section className="team-detail-section">
              <div className="teams-section-heading">
                <div><span className="teams-kicker">Execution graph</span><h2>Workflow</h2></div>
                <span className="team-policy-pill">Max {selectedTeam.max_parallel} parallel</span>
              </div>
              <TeamPreview team={selectedTeam} agents={agents} />
            </section>
            <section className="team-trigger-card">
              <div>
                <span className="teams-kicker">Run this team</span>
                <h2>What should the team accomplish?</h2>
                <p>The objective is passed to every stage. Dependencies receive summaries from the stages before them.</p>
              </div>
              <textarea value={task} onChange={(event) => setTask(event.target.value)} placeholder="e.g. Research the market, compare the top options, and deliver a recommendation…" />
              <div className="team-trigger-foot">
                <span><Check size={13} /> Tool policies and DAG cycles are validated before execution</span>
                <button className="primary-button" disabled={state.pending || !task.trim()} onClick={() => void run()}>
                  <Play size={15} fill="currentColor" /> {state.pending ? 'Starting…' : 'Run team'}
                </button>
              </div>
            </section>
            <TeamRunsPanel teamId={selectedTeam.id} state={state} />
          </>
        ) : (
          <div className="team-detail-empty"><Network size={28} /><h2>Select a team</h2><p>Choose a saved team to inspect its workflow and start a run.</p></div>
        )}
      </main>
    </div>
  );
}

export function TeamsView({ agents, state, onClose }: { agents: Agent[]; state: TeamsState; onClose: () => void }) {
  const [mode, setMode] = useState<TeamsMode>('library');
  const [selectedTeamId, setSelectedTeamId] = useState('');

  const saved = (team: Team) => {
    if (team.id) setSelectedTeamId(team.id);
    setMode('library');
  };

  return (
    <section className="teams-view">
      <header className="teams-header">
        <div className="teams-brand">
          <span className="teams-brand-icon"><Network size={20} /></span>
          <div><h1>Agent teams</h1><p>Design, connect, and run collaborative Hermes workflows.</p></div>
        </div>
        <nav className="teams-mode-tabs" aria-label="Team views">
          <button className={mode === 'library' ? 'active' : ''} onClick={() => setMode('library')}><List size={15} /> Team library</button>
          <button className={mode === 'builder' ? 'active' : ''} onClick={() => setMode('builder')}><GitBranch size={15} /> Create team</button>
        </nav>
        <button className="icon-button" aria-label="Close teams" onClick={onClose}><X size={19} /></button>
      </header>
      {state.error && <div className="teams-error">{state.error}</div>}
      {mode === 'builder'
        ? <TeamBuilder agents={agents} state={state} onSaved={saved} onBack={() => setMode('library')} />
        : (
          <TeamLibrary
            agents={agents}
            state={state}
            selectedTeamId={selectedTeamId}
            setSelectedTeamId={setSelectedTeamId}
            onCreate={() => setMode('builder')}
          />
        )}
    </section>
  );
}
