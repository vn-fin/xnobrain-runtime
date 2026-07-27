import { useEffect, useMemo, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  Archive,
  Check,
  ChevronRight,
  Clock,
  Columns3,
  Coins,
  Brain,
  ExternalLink,
  Link2,
  List,
  Loader2,
  MessageSquare,
  Plus,
  RefreshCw,
  Save,
  Search,
  Terminal,
  Users,
  Workflow,
  Octagon,
  X,
} from 'lucide-react';
import { conversationsApi } from '../api/conversations';
import { ARCHIVED_COLUMN, KANBAN_COLUMNS } from '../api/kanban';
import type { Team } from '../api/teams';
import type { useKanban } from '../hooks/useKanban';
import type {
  Agent,
  ChatMessage,
  ConversationUsage,
  KanbanColumnId,
  KanbanPriority,
  KanbanTask,
} from '../types';
import { formatKanbanEvent } from './kanbanEventFormat';
import { Markdown } from './Markdown';

type KanbanState = ReturnType<typeof useKanban>;

const PRIORITY_LABEL: Record<KanbanPriority, string> = { high: 'High', medium: 'Medium', low: 'Low' };
const ASSIGNEE_COLORS = ['#4f8cff', '#34d399', '#f8d66d', '#c084fc', '#fb923c', '#7dd3fc'];

function monogram(name: string): string {
  return name
    .split(/[\s-]+/)
    .map((word) => word[0])
    .slice(0, 2)
    .join('')
    .toUpperCase();
}

function colorFor(id: string): string {
  let hash = 0;
  for (let i = 0; i < id.length; i += 1) hash = (hash * 31 + id.charCodeAt(i)) >>> 0;
  return ASSIGNEE_COLORS[hash % ASSIGNEE_COLORS.length];
}

function nativeStatusLabel(status: KanbanTask['nativeStatus']): string {
  if (status === 'running') return 'In Progress';
  if (status === 'blocked') return 'Blocked (Error)';
  return status.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function eventTime(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function scheduleTime(value: string | null, timezone: string): string {
  if (!value) return 'No next run';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  try {
    return new Intl.DateTimeFormat([], {
      dateStyle: 'medium',
      timeStyle: 'short',
      timeZone: timezone,
    }).format(parsed);
  } catch {
    return parsed.toLocaleString();
  }
}

function defaultScheduledAt(): string {
  const date = new Date(Date.now() + 60 * 60 * 1000);
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

/** Resolve an assignee id against the real agent roster, with a graceful fallback. */
function resolveAssignee(id: string, agents: Agent[]) {
  const agent = agents.find((item) => item.id === id || item.name === id);
  const name = agent?.title ?? id.replace(/[-_]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
  return { id, name, color: colorFor(id) };
}

function AssigneeStack({ ids, agents }: { ids: string[]; agents: Agent[] }) {
  if (ids.length === 0) return <span className="kb-unassigned">Unassigned</span>;
  return (
    <span className="kb-assignees">
      {ids.slice(0, 3).map((id) => {
        const person = resolveAssignee(id, agents);
        return (
          <span key={id} className="kb-avatar" style={{ background: person.color }} title={person.name}>
            {monogram(person.name)}
          </span>
        );
      })}
      {ids.length > 3 && <span className="kb-avatar more">+{ids.length - 3}</span>}
    </span>
  );
}

function AssigneeSummary({ ids, agents }: { ids: string[]; agents: Agent[] }) {
  if (ids.length === 0) return <span className="kb-unassigned">Unassigned</span>;
  const lead = resolveAssignee(ids[0], agents);
  return (
    <span className="kb-owner">
      <AssigneeStack ids={ids} agents={agents} />
      <span>{lead.name}{ids.length > 1 ? ` +${ids.length - 1}` : ''}</span>
    </span>
  );
}

function AgentPicker({
  agents,
  value,
  onChange,
  disabled = false,
}: {
  agents: Agent[];
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const selected = value ? resolveAssignee(value, agents) : null;
  return (
    <div className="kb-agent-picker">
      <button
        type="button"
        className="kb-agent-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((current) => !current)}
      >
        {selected ? (
          <>
            <span className="kb-avatar" style={{ background: selected.color }}>{monogram(selected.name)}</span>
            <span>{selected.name}</span>
          </>
        ) : (
          <>
            <span className="kb-avatar unassigned">—</span>
            <span>Unassigned</span>
          </>
        )}
        <ChevronRight size={14} />
      </button>
      {open && !disabled && (
        <div className="kb-agent-menu" role="listbox" aria-label="Choose task assignee">
          <button
            type="button"
            role="option"
            aria-selected={!value}
            onClick={() => { onChange(''); setOpen(false); }}
          >
            <span className="kb-avatar unassigned">—</span>
            <span><strong>Unassigned</strong><small>Keep this task waiting</small></span>
            {!value && <Check size={14} />}
          </button>
          {agents.map((agent) => {
            const person = resolveAssignee(agent.id, agents);
            return (
              <button
                type="button"
                role="option"
                aria-selected={value === agent.id}
                key={agent.id}
                onClick={() => { onChange(agent.id); setOpen(false); }}
              >
                <span className="kb-avatar" style={{ background: person.color }}>{monogram(person.name)}</span>
                <span><strong>{person.name}</strong><small>{agent.description || agent.model || 'Assistant'}</small></span>
                {value === agent.id && <Check size={14} />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

function TeamPicker({
  teams,
  value,
  onChange,
}: {
  teams: Team[];
  value: string;
  onChange: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const available = teams.filter((team) => team.enabled);
  const selected = available.find((team) => team.id === value);
  return (
    <div className="kb-agent-picker kb-team-picker">
      <button
        type="button"
        className="kb-agent-trigger kb-team-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        {selected ? (
          <>
            <span className="kb-team-avatar" style={{ background: colorFor(selected.id) }}>
              {monogram(selected.name)}
            </span>
            <span className="kb-team-trigger-copy">
              <strong>{selected.name}</strong>
              <small>{selected.description}</small>
            </span>
          </>
        ) : (
          <>
            <span className="kb-team-avatar empty"><Users size={15} /></span>
            <span className="kb-team-trigger-copy">
              <strong>Choose a saved team</strong>
              <small>{available.length ? `${available.length} available` : 'No enabled teams'}</small>
            </span>
          </>
        )}
        <ChevronRight size={14} />
      </button>
      {open && (
        <div className="kb-agent-menu kb-team-menu" role="listbox" aria-label="Choose agent team">
          {available.length === 0 ? (
            <span className="kb-team-menu-empty">Create and enable a Team before assigning it here.</span>
          ) : available.map((team) => (
            <button
              type="button"
              role="option"
              aria-selected={value === team.id}
              key={team.id}
              onClick={() => { onChange(team.id); setOpen(false); }}
            >
              <span className="kb-team-avatar" style={{ background: colorFor(team.id) }}>
                {monogram(team.name)}
              </span>
              <span>
                <strong>{team.name}</strong>
                <small>{team.description}</small>
                <em>{team.members.length + 1} agents · {team.workflow?.length || team.members.length} stages</em>
              </span>
              {value === team.id && <Check size={14} />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function enabledSkillsFor(agents: Agent[], assignee: string) {
  const agent = agents.find((item) => item.id === assignee || item.name === assignee);
  return agent?.skills.filter((skill) => skill.installed && skill.enabled) ?? [];
}

function SkillPicker({
  agents,
  assignee,
  selected,
  onChange,
}: {
  agents: Agent[];
  assignee: string;
  selected: string[];
  onChange: (skills: string[]) => void;
}) {
  const skills = useMemo(() => enabledSkillsFor(agents, assignee), [agents, assignee]);

  return (
    <div className="kb-skill-picker">
      {!assignee ? (
        <span className="kb-skill-empty">Choose an agent to select its enabled skills.</span>
      ) : skills.length === 0 ? (
        <span className="kb-skill-empty">This agent has no enabled skills.</span>
      ) : (
        <>
          <div className="kb-skill-picker-head">
            <span>{selected.length} of {skills.length} enabled</span>
            <button
              type="button"
              onClick={() => onChange(
                selected.length === skills.length ? [] : skills.map((skill) => skill.skill_id),
              )}
            >
              {selected.length === skills.length ? 'Disable all' : 'Enable all'}
            </button>
          </div>
          <div className="kb-skill-options" role="group" aria-label="Skills used for this task">
            {skills.map((skill) => {
              const checked = selected.includes(skill.skill_id);
              return (
                <label className={checked ? 'selected' : ''} key={skill.skill_id}>
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => onChange(
                      checked
                        ? selected.filter((item) => item !== skill.skill_id)
                        : [...selected, skill.skill_id],
                    )}
                  />
                  <span className="kb-skill-check">{checked && <Check size={12} />}</span>
                  <span>
                    <strong>{skill.name || skill.skill_id}</strong>
                    <small>{skill.description || skill.category}</small>
                  </span>
                </label>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

function PriorityTitle({ task }: { task: KanbanTask }) {
  return (
    <span className="kb-title-line">
      <span className={`kb-prio-bar ${task.priority}`} title={`${PRIORITY_LABEL[task.priority]} priority`} />
      <span className="kb-title-text">{task.title}</span>
    </span>
  );
}

function TaskCard({
  task,
  agents,
  statusLabel,
  column,
  onOpen,
  onDragStart,
  onDragEnd,
}: {
  task: KanbanTask;
  agents: Agent[];
  statusLabel: (status: string) => string;
  column: KanbanColumnId;
  onOpen: () => void;
  onDragStart: () => void;
  onDragEnd: () => void;
}) {
  const scheduleCompleted = task.schedule?.recurrence === 'once'
    && task.schedule.occurrenceCount > 0
    && task.schedule.nextRunAt == null;
  return (
    <button
      className={`kb-card col-${column} status-${task.nativeStatus}`}
      draggable={!task.team && column !== 'archived' && task.nativeStatus !== 'scheduled'}
      onDragStart={(event) => {
        if (column === 'archived' || task.nativeStatus === 'scheduled') return;
        event.dataTransfer.effectAllowed = 'move';
        event.dataTransfer.setData('text/plain', task.id);
        onDragStart();
      }}
      onDragEnd={onDragEnd}
      onClick={onOpen}
      aria-label={`Open ${task.id}: ${task.title}`}
    >
      <div className="kb-card-top">
        <span className="kb-id">{task.id}</span>
        <span className={`kb-column-state col-${task.status}`}>{statusLabel(task.status)}</span>
        <span className={`kb-substate native-${task.nativeStatus}`}>{nativeStatusLabel(task.nativeStatus)}</span>
      </div>
      <PriorityTitle task={task} />
      {task.team && (
        <div className="kb-team-badge">
          <Users size={13} />
          <span>{task.team.name}</span>
          <strong>{task.team.nodes.filter((node) => node.status === 'running').length} active</strong>
        </div>
      )}
      <p className="kb-card-desc">{task.description}</p>
      {task.tags.length > 0 && (
        <div className="kb-tags">
          {task.tags.map((tag) => (
            <span key={tag} className="kb-tag">#{tag}</span>
          ))}
        </div>
      )}
      {task.schedule ? (
        <div className={`kb-signal scheduled ${task.schedule.enabled ? '' : 'paused'}`}>
          <Clock size={13} />
          <span>
            {scheduleCompleted
              ? 'Schedule completed'
              : task.schedule.enabled
              ? `Next: ${scheduleTime(task.schedule.nextRunAt, task.schedule.timezone)}`
              : 'Schedule paused'}
          </span>
        </div>
      ) : task.block ? (
        <div className="kb-signal blocked">
          <AlertTriangle size={13} />
          <span>{task.block}</span>
        </div>
      ) : task.summary ? (
        <div className="kb-signal done">
          <Check size={13} />
          <span>{task.summary}</span>
        </div>
      ) : column === 'running' ? (
        <div className="kb-progress">
          <div className="kb-progress-label">
            <span>
              <Clock size={12} /> Running
            </span>
            <span>{task.progress}%</span>
          </div>
          <div className="kb-track">
            <span style={{ width: `${task.progress}%` }} />
          </div>
        </div>
      ) : task.deps.length > 0 ? (
        <div className="kb-signal">
          <Link2 size={13} /> {task.deps.filter((dep) => dep.state === 'done').length}/{task.deps.length} dependencies
        </div>
      ) : null}
      <div className="kb-card-foot">
        <AssigneeSummary ids={task.assignees} agents={agents} />
        <span className="kb-updated">{task.updated}</span>
      </div>
    </button>
  );
}

function KanbanConversationModal({
  task,
  agents,
  onClose,
}: {
  task: KanbanTask;
  agents: Agent[];
  onClose: () => void;
}) {
  const link = task.conversation;
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [usage, setUsage] = useState<ConversationUsage>();
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [error, setError] = useState('');
  const assignee = resolveAssignee(link?.agentId ?? task.assignees[0] ?? '', agents);

  useEffect(() => {
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', close);
    return () => window.removeEventListener('keydown', close);
  }, [onClose]);

  useEffect(() => {
    if (!link) return undefined;
    const controller = new AbortController();
    setStatus('loading');
    void Promise.allSettled([
      conversationsApi.messages(link.agentId, link.id, controller.signal),
      conversationsApi.usage(link.agentId, link.id, controller.signal),
    ]).then(([messageResult, usageResult]) => {
      if (controller.signal.aborted) return;
      if (messageResult.status === 'rejected') {
        setError(messageResult.reason instanceof Error
          ? messageResult.reason.message
          : 'Could not load this conversation.');
        setStatus('error');
        return;
      }
      setMessages(messageResult.value);
      if (usageResult.status === 'fulfilled') setUsage(usageResult.value);
      setStatus('ready');
    });
    return () => controller.abort();
  }, [link]);

  const visibleMessages = messages.filter((message) =>
    message.role === 'user'
    || (message.role === 'assistant' && message.content.trim().length > 0),
  );

  return (
    <div className="modal-overlay team-conversation-overlay" onClick={(event) => {
      event.stopPropagation();
      onClose();
    }}>
      <div
        className="app-modal team-conversation-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="kanban-conversation-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="team-conversation-head">
          <span className={`run-node-state ${task.nativeStatus}`}>
            {task.nativeStatus === 'running' ? <Loader2 size={16} /> : <Check size={15} />}
          </span>
          <span>
            <strong id="kanban-conversation-title">{task.title}</strong>
            <small>{assignee.name} · {nativeStatusLabel(task.nativeStatus)}</small>
          </span>
          <button className="icon-button" onClick={onClose} aria-label="Close task conversation">
            <X size={17} />
          </button>
        </header>
        <section className="team-conversation-metrics" aria-label="Conversation metrics">
          <span><Coins size={14} /><small>Tokens</small><strong>{usage ? usage.totalTokens.toLocaleString() : '—'}</strong></span>
          <span><Brain size={14} /><small>Steps</small><strong>{usage?.steps != null ? usage.steps.toLocaleString() : '—'}</strong></span>
          <span><Clock size={14} /><small>Execution time</small><strong>{usage?.executionSeconds != null ? `${usage.executionSeconds.toFixed(1)}s` : '—'}</strong></span>
          <span><MessageSquare size={14} /><small>Messages</small><strong>{usage ? usage.messages.toLocaleString() : visibleMessages.length.toLocaleString()}</strong></span>
        </section>
        <div className="message-canvas team-conversation-canvas">
          {status === 'loading' ? (
            <div className="team-conversation-empty">
              <Loader2 className="run-step-spin" size={22} />
              <strong>Loading conversation…</strong>
            </div>
          ) : status === 'error' ? (
            <div className="team-conversation-empty error">
              <X size={22} /><strong>Conversation unavailable</strong><p>{error}</p>
            </div>
          ) : visibleMessages.length === 0 ? (
            <div className="team-conversation-empty"><MessageSquare size={22} /><strong>No stored messages</strong></div>
          ) : visibleMessages.map((message) => (
            message.role === 'user'
              ? <div className="user-bubble" key={message.id}><Markdown content={message.content} /></div>
              : <article className="assistant-message" key={message.id}><div className="message-content"><Markdown content={message.content} /></div></article>
          ))}
        </div>
        <footer className="team-conversation-foot">
          {link && <a className="kb-conversation-link" href={link.url}>Open tracking URL <ExternalLink size={13} /></a>}
          <button className="conn-btn" onClick={onClose}>Close</button>
        </footer>
      </div>
    </div>
  );
}

function TaskDrawer({
  task,
  agents,
  state,
  onMove,
  onArchive,
  onClose,
}: {
  task: KanbanTask;
  agents: Agent[];
  state: KanbanState;
  onMove: (taskId: string, status: KanbanColumnId) => void;
  onArchive: (task: KanbanTask) => void;
  onClose: () => void;
}) {
  const board = state.board;
  const editable = !task.team && ['triage', 'todo', 'ready', 'scheduled'].includes(task.nativeStatus);
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(task.title);
  const [description, setDescription] = useState(task.description);
  const [priority, setPriority] = useState(task.priority);
  const [skills, setSkills] = useState(() => {
    const enabled = enabledSkillsFor(agents, task.assignees[0] ?? '');
    return task.skills.filter((skill) => enabled.some((item) => item.skill_id === skill));
  });
  const [comment, setComment] = useState('');
  const [saving, setSaving] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [conversationOpen, setConversationOpen] = useState(false);
  const movableStatuses = task.allowedStatuses.filter((status) => status !== 'archived');
  const scheduleCompleted = task.schedule?.recurrence === 'once'
    && task.schedule.occurrenceCount > 0
    && task.schedule.nextRunAt == null;

  useEffect(() => {
    if (editing) return;
    setTitle(task.title);
    setDescription(task.description);
    setPriority(task.priority);
    const enabled = enabledSkillsFor(agents, task.assignees[0] ?? '');
    setSkills(task.skills.filter((skill) => enabled.some((item) => item.skill_id === skill)));
  }, [agents, editing, task.assignees, task.description, task.priority, task.skills, task.title]);

  if (!board) return null;

  const save = async () => {
    if (!title.trim() || !description.trim()) return;
    setSaving(true);
    const updated = await state.updateTask(task.id, {
      title: title.trim(),
      description: description.trim(),
      priority,
      skills,
    });
    setSaving(false);
    if (updated) setEditing(false);
  };

  const submitComment = async () => {
    if (!comment.trim()) return;
    const saved = await state.addComment(task.id, comment.trim());
    if (saved) setComment('');
  };

  const cancel = async () => {
    setCancelling(true);
    try {
      await state.cancelTask(task.id);
    } finally {
      setCancelling(false);
    }
  };

  return (
    <div className="kb-overlay" onClick={onClose}>
      <div className="kb-drawer" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
        <div className="kb-drawer-head">
          <div>
            <div className="kb-drawer-kicker">
              <span className="kb-id">{task.id}</span>
              <span className={`kb-column-state col-${task.status}`}>{state.statusLabel(task.status)}</span>
              <span className={`kb-substate native-${task.nativeStatus}`}>{nativeStatusLabel(task.nativeStatus)}</span>
            </div>
            <h2>{task.title}</h2>
          </div>
          <div className="kb-drawer-head-actions">
            {!task.team && task.nativeStatus === 'running' && (
              <button
                className="conn-btn danger-solid"
                disabled={cancelling}
                onClick={() => void cancel()}
              >
                {cancelling ? <Loader2 size={14} /> : <Octagon size={14} />}
                {cancelling ? 'Cancelling…' : 'Cancel task'}
              </button>
            )}
            <button
              className="icon-button"
              aria-label="Refresh task details"
              title="Refresh task details"
              onClick={() => void state.refreshTask(task.id)}
            >
              <RefreshCw size={16} className={state.detailLoading ? 'spinning' : ''} />
            </button>
            <button className="icon-button" aria-label="Close task details" onClick={onClose}>
              <X size={18} />
            </button>
          </div>
        </div>
        <div className="kb-drawer-body">
          <section className="kb-detail-section kb-edit-section">
            <div className="kb-section-heading">
              <div>
                <span className="kb-label">Task brief</span>
                <small>Name and instructions given to the agent.</small>
              </div>
              {editable && !editing && (
                <button className="kb-text-action" onClick={() => setEditing(true)}>Edit task</button>
              )}
            </div>
            {editing ? (
              <div className="kb-edit-form">
                <label className={!title.trim() ? 'kb-field invalid' : 'kb-field'}>
                  Task name
                  <input value={title} onChange={(event) => setTitle(event.target.value)} />
                </label>
                <label className={!description.trim() ? 'kb-field invalid' : 'kb-field'}>
                  Description
                  <textarea value={description} onChange={(event) => setDescription(event.target.value)} />
                  <small className="kb-field-help">Required. Write the outcome and constraints the agent should follow.</small>
                </label>
                <label className="kb-field">
                  Priority
                  <select value={priority} onChange={(event) => setPriority(event.target.value as KanbanPriority)}>
                    <option value="high">High</option>
                    <option value="medium">Medium</option>
                    <option value="low">Low</option>
                  </select>
                </label>
                <div className="kb-field">
                  Skills used for this task
                  <SkillPicker
                    agents={agents}
                    assignee={task.assignees[0] ?? ''}
                    selected={skills}
                    onChange={setSkills}
                  />
                </div>
                <div className="kb-edit-actions">
                  <button className="conn-btn ghost" onClick={() => setEditing(false)}>Cancel</button>
                  <button
                    className="primary-button"
                    disabled={saving || !title.trim() || !description.trim()}
                    onClick={() => void save()}
                  >
                    <Save size={15} /> {saving ? 'Saving…' : 'Save changes'}
                  </button>
                </div>
              </div>
            ) : (
              <>
                <p className="kb-desc">{task.description}</p>
                {task.skills.length > 0 && (
                  <div className="kb-task-skills">
                    {task.skills.map((skill) => <span key={skill}>{skill}</span>)}
                  </div>
                )}
              </>
            )}
            {task.block && (
              <div className="kb-signal blocked">
                <AlertTriangle size={14} />
                <span>{task.block}</span>
              </div>
            )}
            {task.summary && (
              <div className="kb-signal done">
                <Check size={14} />
                <span>{task.summary}</span>
              </div>
            )}
          </section>

          <section className="kb-detail-section kb-drawer-grid">
            <div className="kb-owner-field">
              <span className="kb-label">{task.team ? 'Agent team' : 'Assignee'}</span>
              {task.team ? (
                <span className="kb-team-owner"><Users size={15} /> {task.team.name}</span>
              ) : (
                <AgentPicker
                  agents={agents}
                  value={task.assignees[0] ?? ''}
                  disabled={!editable}
                  onChange={(value) => void state.assignTask(task.id, value || null)}
                />
              )}
              {editable && <small className="kb-field-help">One agent owns and runs each task.</small>}
            </div>
            <div>
              <span className="kb-label">Priority</span>
              <span className="kb-value">
                <span className={`kb-prio-bar ${task.priority}`} /> {PRIORITY_LABEL[task.priority]}
              </span>
            </div>
            <div>
              <span className="kb-label">Progress</span>
              <span className="kb-value">{task.progress}%</span>
            </div>
          </section>

          {task.team && (
            <section className="kb-detail-section kb-team-run">
              <div className="kb-section-heading">
                <div>
                  <span className="kb-label">Team workflow</span>
                  <small>Live Hermes task state for every DAG node.</small>
                </div>
                <span className={`kb-team-run-state ${task.team.status}`}>
                  <span /> {task.team.status.replaceAll('_', ' ')}
                </span>
              </div>
              <div className="kb-team-progress">
                <span style={{ width: `${task.team.progress}%` }} />
              </div>
              <div className="kb-team-dag" aria-label={`${task.team.name} workflow`}>
                {task.team.nodes.map((node) => {
                  const person = resolveAssignee(node.agentId, agents);
                  const active = node.status === 'running';
                  return (
                    <div className={`kb-team-node ${node.status}${active ? ' active' : ''}`} key={node.taskId}>
                      <div className="kb-team-node-deps">
                        {node.needs.length ? <><Workflow size={11} /> {node.needs.join(', ')}</> : 'Start'}
                      </div>
                      <div className="kb-team-node-main">
                        <span className="kb-avatar" style={{ background: person.color }}>{monogram(person.name)}</span>
                        <span>
                          <strong>{node.role}</strong>
                          <small>{person.name}</small>
                        </span>
                        <span className={`kb-substate native-${node.status}`}>
                          {nativeStatusLabel(node.status as KanbanTask['nativeStatus'])}
                        </span>
                      </div>
                      {node.summary && <p>{node.summary}</p>}
                      {active && <div className="kb-team-node-pulse" />}
                    </div>
                  );
                })}
              </div>
              {!task.team.cancelled && !['done', 'cancelled'].includes(task.team.status) && (
                <button className="conn-btn danger-solid kb-team-cancel" onClick={() => void state.cancelTeamTask(task.id)}>
                  <Octagon size={14} /> Cancel team run
                </button>
              )}
            </section>
          )}

          {task.schedule && (
            <section className="kb-detail-section kb-schedule-detail">
              <div className="kb-section-heading">
                <div>
                  <span className="kb-label">Schedule</span>
                  <small>
                    {task.schedule.recurrence === 'interval'
                      ? `Repeats every ${task.schedule.intervalMinutes} minutes`
                      : 'Runs once'}
                  </small>
                </div>
                <span className={`kb-schedule-state ${scheduleCompleted ? 'completed' : task.schedule.enabled ? 'active' : 'paused'}`}>
                  {scheduleCompleted ? 'Completed' : task.schedule.enabled ? 'Active' : 'Paused'}
                </span>
              </div>
              <div className="kb-schedule-time">
                <Clock size={16} />
                <div>
                  <strong>
                    {scheduleCompleted
                      ? `Ran ${scheduleTime(task.schedule.lastRunAt, task.schedule.timezone)}`
                      : scheduleTime(task.schedule.nextRunAt, task.schedule.timezone)}
                  </strong>
                  <small>{task.schedule.timezone} · {task.schedule.occurrenceCount} run{task.schedule.occurrenceCount === 1 ? '' : 's'}</small>
                </div>
              </div>
              {!scheduleCompleted && <div className="kb-schedule-actions">
                <button
                  className="conn-btn ghost"
                  onClick={() => void state.updateSchedule(
                    task.id,
                    task.schedule?.enabled ? 'pause' : 'resume',
                  )}
                >
                  {task.schedule.enabled ? 'Pause schedule' : 'Resume schedule'}
                </button>
                <button className="conn-btn" onClick={() => void state.updateSchedule(task.id, 'run_now')}>
                  Run now
                </button>
              </div>}
            </section>
          )}

          {task.conversation && (
            <section className="kb-detail-section kb-conversation-tracking">
              <div className="kb-section-heading">
                <div>
                  <span className="kb-label">Conversation tracking</span>
                  <small>Follow the worker’s stored Hermes conversation.</small>
                </div>
                <button className="kb-text-action" onClick={() => setConversationOpen(true)}>
                  View conversation
                </button>
              </div>
              <a className="kb-conversation-link" href={task.conversation.url}>
                {task.conversation.url} <ExternalLink size={13} />
              </a>
              <code className="kb-conversation-id">{task.conversation.id}</code>
            </section>
          )}

          {task.result && (
            <section className="kb-detail-section kb-result-section">
              <div className="kb-section-heading">
                <div>
                  <span className="kb-label">Result</span>
                  <small>Final handoff from the worker.</small>
                </div>
              </div>
              <div className="kb-result">{task.result}</div>
            </section>
          )}

          {task.deps.length > 0 && (
            <section className="kb-detail-section">
              <span className="kb-label">Dependencies</span>
              <div className="kb-deps">
                {task.deps.map((dep) => (
                  <div key={dep.id} className="kb-dep">
                    <Link2 size={13} />
                    <span className="kb-id">{dep.id}</span> {dep.title}
                    <span className={`kb-dep-state ${dep.state}`}>{dep.state}</span>
                  </div>
                ))}
              </div>
            </section>
          )}

          <section className="kb-detail-section">
            <div className="kb-section-heading">
              <div>
                <span className="kb-label">Comments ({task.comments.length})</span>
                <small>Add context for the next worker attempt.</small>
              </div>
            </div>
            <div className="kb-comments">
              {task.comments.length === 0 && <span className="kb-muted">No comments yet.</span>}
              {task.comments.map((item) => (
                <article className="kb-comment" key={item.id}>
                  <header><strong>{item.author}</strong><time>{eventTime(item.createdAt)}</time></header>
                  <p>{item.body}</p>
                </article>
              ))}
            </div>
            {task.nativeStatus !== 'archived' && (
              <div className="kb-comment-compose">
                <textarea
                  aria-label="Add task comment"
                  value={comment}
                  placeholder="Add a note, decision, or missing context…"
                  onChange={(event) => setComment(event.target.value)}
                />
                <button className="primary-button" disabled={!comment.trim()} onClick={() => void submitComment()}>
                  <MessageSquare size={14} /> Add comment
                </button>
              </div>
            )}
          </section>

          <section className="kb-detail-section">
            <div className="kb-section-heading">
              <div>
                <span className="kb-label">Worker activity</span>
                <small>Sensitive prompts, arguments, and tool output are omitted.</small>
              </div>
              <span className="kb-byte-count">{task.workerActivity?.sizeBytes ?? 0} B log</span>
            </div>
            {!task.workerActivity?.exists ? (
              <span className="kb-muted">No worker activity yet.</span>
            ) : task.workerActivity.entries.length === 0 ? (
              <span className="kb-muted">The worker started, but no structured tool activity is available.</span>
            ) : (
              <div className="kb-worker-activity">
                {task.workerActivity.entries.map((entry, index) => (
                  <div key={`${entry.name}-${index}`}>
                    <Terminal size={13} />
                    <strong>{entry.name.replaceAll('_', ' ')}</strong>
                    <span>{entry.durationSeconds.toFixed(1)}s</span>
                  </div>
                ))}
              </div>
            )}
          </section>

          {task.runs.length > 0 && (
            <section className="kb-detail-section">
              <span className="kb-label">Run history ({task.runs.length})</span>
              <div className="kb-runs">
                {[...task.runs].reverse().map((run) => (
                  <article key={run.id}>
                    <header>
                      <span className={`kb-run-state ${run.outcome || run.status}`}>{run.outcome || run.status}</span>
                      <strong>@{run.profile || 'unassigned'}</strong>
                      <time>{eventTime(run.startedAt)}</time>
                    </header>
                    {run.summary && <p>{run.summary}</p>}
                  </article>
                ))}
              </div>
            </section>
          )}

          <section className="kb-detail-section">
            <div className="kb-section-heading">
              <div>
                <span className="kb-label">Event log ({task.events.length})</span>
                <small>Durable lifecycle changes for this task.</small>
              </div>
              <Activity size={15} />
            </div>
            <div className="kb-event-timeline">
              {task.events.length === 0 && <span className="kb-muted">No events yet.</span>}
              {[...task.events].reverse().map((event) => {
                const presentation = formatKanbanEvent(event, (agentId) => resolveAssignee(agentId, agents).name);
                return (
                  <div className={`kb-event ${presentation.tone}`} key={event.id}>
                    <span className="kb-event-marker" />
                    <div>
                      <header><strong>{presentation.title}</strong><time>{eventTime(event.createdAt)}</time></header>
                      <p>{presentation.description}</p>
                      {presentation.details.length > 0 && (
                        <ul>
                          {presentation.details.map((detail) => <li key={detail}>{detail}</li>)}
                        </ul>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </section>
        </div>
        {task.status !== 'archived' && <div className="kb-drawer-foot">
          <div>
            <span className="kb-label">Move task</span>
            <p>
              {task.nativeStatus === 'scheduled'
                ? 'Scheduled tasks are controlled by their schedule.'
                : 'Only valid next steps are enabled.'}
            </p>
          </div>
          <label className="kb-move-select">
            <span className="sr-only">Move task to</span>
            <select
              aria-label="Move task to"
              value={task.status}
              disabled={movableStatuses.length === 0}
              onChange={(event) => onMove(task.id, event.target.value as KanbanColumnId)}
            >
              {board.statuses.filter((status) => status.id !== 'archived').map((status) => (
                <option
                  key={status.id}
                  value={status.id}
                  disabled={
                    status.id !== task.status
                    && !movableStatuses.includes(status.id as (typeof movableStatuses)[number])
                  }
                >
                  {status.label}{status.id === task.status ? ' · current' : ''}
                </option>
              ))}
            </select>
          </label>
          {task.allowedStatuses.includes('archived') && (
            <button className="kb-archive-button" onClick={() => onArchive(task)}>
              <Archive size={15} /> Archive task
            </button>
          )}
        </div>}
      </div>
      {conversationOpen && task.conversation && (
        <KanbanConversationModal task={task} agents={agents} onClose={() => setConversationOpen(false)} />
      )}
    </div>
  );
}

function NewTaskModal({
  agents,
  teams,
  state,
  initialStatus,
  onClose,
}: {
  agents: Agent[];
  teams: Team[];
  state: KanbanState;
  initialStatus?: string;
  onClose: () => void;
}) {
  const board = state.board;
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [status, setStatus] = useState<'backlog' | 'todo' | 'scheduled'>(
    initialStatus === 'backlog' ? 'backlog' : 'todo',
  );
  const [priority, setPriority] = useState<KanbanPriority>('medium');
  const [assignee, setAssignee] = useState('');
  const [assignmentType, setAssignmentType] = useState<'agent' | 'team'>('agent');
  const [teamId, setTeamId] = useState('');
  const [skills, setSkills] = useState<string[]>([]);
  const [recurrence, setRecurrence] = useState<'once' | 'interval'>('once');
  const [scheduledAt, setScheduledAt] = useState(defaultScheduledAt);
  const [intervalMinutes, setIntervalMinutes] = useState(60);
  const [scheduleTimezone] = useState(
    () => Intl.DateTimeFormat().resolvedOptions().timeZone || 'Etc/UTC',
  );
  const [invalid, setInvalid] = useState(false);

  useEffect(() => {
    setSkills(enabledSkillsFor(agents, assignee).map((skill) => skill.skill_id));
  }, [assignee]);

  if (!board) return null;

  const submit = async () => {
    if (!title.trim() || !description.trim() || (status === 'scheduled' && !scheduledAt)) {
      setInvalid(true);
      return;
    }
    await state.createTask({
      title: title.trim(),
      description: description.trim(),
      status: status as KanbanColumnId,
      priority,
      assignee: assignmentType === 'agent' ? assignee || null : null,
      teamId: assignmentType === 'team' ? teamId : null,
      skills: assignmentType === 'agent' ? skills : [],
      schedule: status === 'scheduled' ? {
        recurrence,
        scheduled_at: scheduledAt,
        timezone: scheduleTimezone,
        ...(recurrence === 'interval' ? { interval_minutes: intervalMinutes } : {}),
      } : undefined,
    });
    onClose();
  };

  return (
    <div className="kb-overlay" onClick={onClose}>
      <div className="kb-modal" role="dialog" aria-modal="true" aria-labelledby="new-task-title" onClick={(event) => event.stopPropagation()}>
        <div className="kb-modal-head">
          <h2 id="new-task-title">New task</h2>
          <button className="icon-button" aria-label="Close" onClick={onClose}>
            <X size={18} />
          </button>
        </div>
        <div className="kb-modal-body">
          <label className={invalid && !title.trim() ? 'kb-field invalid' : 'kb-field'}>
            Title
            <input
              value={title}
              autoFocus
              placeholder="Describe the task…"
              onChange={(event) => setTitle(event.target.value)}
            />
          </label>
          <div className="kb-field-row">
            <label className="kb-field">
              Status
              <select
                value={status}
                onChange={(event) => setStatus(event.target.value as 'backlog' | 'todo' | 'scheduled')}
              >
                <option value="backlog">Backlog</option>
                <option value="todo">Todo</option>
                <option value="scheduled">Scheduled</option>
              </select>
            </label>
            <label className="kb-field">
              Priority
              <select value={priority} onChange={(event) => setPriority(event.target.value as KanbanPriority)}>
                <option value="high">High</option>
                <option value="medium">Medium</option>
                <option value="low">Low</option>
              </select>
            </label>
          </div>
          <div className="kb-field">
            Run with
            <div className="kb-assignment-tabs">
              <button type="button" className={assignmentType === 'agent' ? 'active' : ''} onClick={() => setAssignmentType('agent')}>
                One agent
              </button>
              <button type="button" className={assignmentType === 'team' ? 'active' : ''} onClick={() => { setAssignmentType('team'); setStatus('todo'); }}>
                Agent team
              </button>
            </div>
            {assignmentType === 'agent' ? (
              <>
                <AgentPicker agents={agents} value={assignee} onChange={setAssignee} />
                <small className="kb-field-help">One agent owns and runs this task.</small>
              </>
            ) : (
              <>
                <TeamPicker teams={teams} value={teamId} onChange={setTeamId} />
                <small className="kb-field-help">The saved DAG expands into native Hermes Kanban tasks.</small>
              </>
            )}
          </div>
          {assignmentType === 'team' && teamId && (() => {
            const team = teams.find((item) => item.id === teamId);
            if (!team) return null;
            const steps = team.workflow?.length
              ? team.workflow
              : team.members.filter((member) => member.enabled).map((member, index) => ({
                  id: `worker-${index + 1}`,
                  role: member.role,
                  agent_id: member.agent_id,
                  needs: [] as string[],
                }));
            return (
              <div className="kb-team-preview">
                <div><Users size={15} /><strong>{team.name}</strong><span>{steps.length + 1} nodes</span></div>
                <p>{team.description}</p>
                <div className="kb-team-preview-flow">
                  {steps.map((step) => (
                    <span key={step.id}>
                      <small>{step.needs?.length ? step.needs.join(' + ') : 'Start'}</small>
                      <strong>{step.role || 'worker'}</strong>
                    </span>
                  ))}
                  <span className="coordinator"><small>Final</small><strong>Synthesis</strong></span>
                </div>
              </div>
            );
          })()}
          <label className={invalid && !description.trim() ? 'kb-field invalid' : 'kb-field'}>
            Description <span className="kb-required">Required</span>
            <textarea
              value={description}
              placeholder="Define the expected outcome, source material, and constraints the agent should follow…"
              onChange={(event) => setDescription(event.target.value)}
            />
            <small className="kb-field-help">The assigned agent uses this as its working brief.</small>
          </label>
          {assignmentType === 'agent' && status === 'scheduled' && (
            <div className="kb-schedule-form">
              <div className="kb-schedule-form-head">
                <Clock size={17} />
                <div>
                  <strong>Run later</strong>
                  <small>The Kanban dispatcher starts this task when it is due.</small>
                </div>
              </div>
              <div className="kb-field-row">
                <label className="kb-field">
                  Repeat
                  <select
                    value={recurrence}
                    onChange={(event) => setRecurrence(event.target.value as 'once' | 'interval')}
                  >
                    <option value="once">Run once</option>
                    <option value="interval">Repeat</option>
                  </select>
                </label>
                {recurrence === 'interval' && (
                  <label className="kb-field">
                    Every (minutes)
                    <input
                      type="number"
                      min={1}
                      value={intervalMinutes}
                      onChange={(event) => setIntervalMinutes(Math.max(1, Number(event.target.value)))}
                    />
                  </label>
                )}
              </div>
              <label className={!scheduledAt ? 'kb-field invalid' : 'kb-field'}>
                First run
                <input
                  type="datetime-local"
                  value={scheduledAt}
                  onChange={(event) => setScheduledAt(event.target.value)}
                />
              </label>
              <div className="kb-schedule-preview">
                <Clock size={14} />
                <span>
                  {recurrence === 'interval' ? `Every ${intervalMinutes} minutes, starting ` : 'Runs '}
                  {scheduledAt ? new Date(scheduledAt).toLocaleString() : 'after a date is selected'}
                  {' · '}{scheduleTimezone}
                </span>
              </div>
            </div>
          )}
          {assignmentType === 'agent' && <div className="kb-field">
            Skills used for this task
            <SkillPicker agents={agents} assignee={assignee} selected={skills} onChange={setSkills} />
            <small className="kb-field-help">Skills are loaded for this task only.</small>
          </div>}
        </div>
        <div className="kb-modal-foot">
          <button className="conn-btn ghost" onClick={onClose}>Cancel</button>
          <button
            className="primary-button"
            disabled={!title.trim() || !description.trim() || (assignmentType === 'team' && !teamId) || (status === 'scheduled' && !scheduledAt)}
            onClick={() => void submit()}
          >
            <Plus size={16} /> Create task
          </button>
        </div>
      </div>
    </div>
  );
}

function ArchiveConfirm({
  task,
  onCancel,
  onConfirm,
}: {
  task: KanbanTask;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="kb-overlay kb-confirm-overlay" onClick={onCancel}>
      <div
        className="kb-modal kb-confirm-modal"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="archive-task-title"
        aria-describedby="archive-task-description"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="kb-confirm-icon"><Archive size={22} /></div>
        <div className="kb-confirm-copy">
          <h2 id="archive-task-title">Archive this task?</h2>
          <p id="archive-task-description">
            “{task.title}” will leave the current board and remain available in the Archived tab.
          </p>
        </div>
        <div className="kb-confirm-actions">
          <button className="conn-btn ghost" onClick={onCancel}>Keep task</button>
          <button className="conn-btn danger-solid" onClick={onConfirm}>
            <Archive size={14} /> Archive task
          </button>
        </div>
      </div>
    </div>
  );
}

export function KanbanView({
  teams,
  agents,
  state,
  onClose,
}: {
  teams: Team[];
  agents: Agent[];
  state: KanbanState;
  onClose: () => void;
}) {
  const { board, view, setView, search, setSearch, visibleTasks, columnOf, statusLabel } = state;
  const [openTaskId, setOpenTaskId] = useState<string | null>(null);
  const [newTaskOpen, setNewTaskOpen] = useState(false);
  const [newTaskStatus, setNewTaskStatus] = useState<string | undefined>(undefined);
  const [taskScope, setTaskScope] = useState<'current' | 'archived'>('current');
  const [archiveTask, setArchiveTask] = useState<KanbanTask | null>(null);
  const [agentFilter, setAgentFilter] = useState('all');
  const [priorityFilter, setPriorityFilter] = useState<'all' | KanbanPriority>('all');
  const [columnFilter, setColumnFilter] = useState<'all' | KanbanColumnId>('all');
  const [draggedTaskId, setDraggedTaskId] = useState<string | null>(null);
  const [dropColumn, setDropColumn] = useState<KanbanColumnId | null>(null);

  const openTask = useMemo(
    () => (openTaskId ? board?.tasks.find((task) => task.id === openTaskId) ?? null : null),
    [board, openTaskId],
  );

  useEffect(() => {
    if (!state.requestedTaskId || !board) return;
    const requested = board.tasks.find((task) => task.id === state.requestedTaskId);
    if (!requested) return;
    setTaskScope(requested.status === 'archived' ? 'archived' : 'current');
    setOpenTaskId(requested.id);
    void state.refreshTask(requested.id);
    state.clearRequestedTask();
  }, [board, state]);

  const assigneeOptions = useMemo(() => {
    const ids = new Set(board?.tasks.flatMap((task) => task.assignees) ?? []);
    return [...ids]
      .map((id) => resolveAssignee(id, agents))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [agents, board]);

  const filteredTasks = useMemo(
    () =>
      visibleTasks.filter(
        (task) =>
          (taskScope === 'archived' ? task.status === 'archived' : task.status !== 'archived') &&
          (agentFilter === 'all' || task.assignees.includes(agentFilter)) &&
          (priorityFilter === 'all' || task.priority === priorityFilter) &&
          (columnFilter === 'all' || columnOf(task.status) === columnFilter),
      ),
    [agentFilter, columnFilter, columnOf, priorityFilter, taskScope, visibleTasks],
  );

  const metrics = useMemo(() => {
    const tasks = (board?.tasks ?? []).filter((task) => task.status !== 'archived');
    const running = tasks.filter((task) => columnOf(task.status) === 'running');
    const blocked = tasks.filter((task) => Boolean(task.block));
    const completed = tasks.filter(
      (task) => columnOf(task.status) === 'done' && !task.block,
    );
    return {
      total: tasks.length,
      running: running.length,
      activeAgents: new Set(running.flatMap((task) => task.assignees)).size,
      blocked: blocked.length,
      completed: completed.length,
      completion: tasks.length ? Math.round((completed.length / tasks.length) * 100) : 0,
    };
  }, [board, columnOf]);

  const filtersActive =
    Boolean(search.trim()) || agentFilter !== 'all' || priorityFilter !== 'all' || columnFilter !== 'all';

  const clearFilters = () => {
    setSearch('');
    setAgentFilter('all');
    setPriorityFilter('all');
    setColumnFilter('all');
  };

  const moveTask = (taskId: string, status: KanbanColumnId) => {
    const task = board?.tasks.find((item) => item.id === taskId);
    if (!task || columnOf(task.status) === status) return;
    if (!task.allowedStatuses.includes(status)) return;
    if (status === 'archived') return;
    void state.moveTask(taskId, status);
  };

  const confirmArchive = () => {
    if (!archiveTask) return;
    void state.moveTask(archiveTask.id, 'archived');
    setOpenTaskId(null);
    setArchiveTask(null);
  };

  const currentCount = board?.tasks.filter((task) => task.status !== 'archived').length ?? 0;
  const archivedCount = board?.tasks.filter((task) => task.status === 'archived').length ?? 0;

  return (
    <section className="kanban-view">
      <header className="kb-header">
        <div className="kb-title-group">
          <div className="kb-heading-line">
            <span className="kb-heading-icon" style={board ? { color: board.color } : undefined}>
              <Columns3 size={18} />
            </span>
            <div className="kb-board-picker-wrap">
              <label>
                <span className="sr-only">Select task board</span>
                <select
                  className="kb-board-picker"
                  value={state.activeBoardId}
                  onChange={(event) => {
                    state.setActiveBoardId(event.target.value);
                    setAgentFilter('all');
                    setPriorityFilter('all');
                    setColumnFilter('all');
                    setOpenTaskId(null);
                  }}
                >
                  {state.boards.map((item) => (
                    <option key={item.id} value={item.id}>{item.name} · {item.tasks.length} tasks</option>
                  ))}
                </select>
              </label>
              <span className="kb-board-id">board/{board?.id ?? 'default'}</span>
              <span className={`kb-live-status ${state.liveStatus}`}>
                <span />
                {state.liveStatus === 'live' ? 'Live' : state.liveStatus === 'connecting' ? 'Connecting' : 'Reconnecting'}
              </span>
            </div>
          </div>
          <span className="kb-sub">{board?.description}</span>
        </div>
        <div className="kb-header-actions">
          <div className="kb-scope-switch" role="tablist" aria-label="Task visibility">
            <button
              className={taskScope === 'current' ? 'active' : ''}
              role="tab"
              aria-selected={taskScope === 'current'}
              onClick={() => {
                setTaskScope('current');
                setColumnFilter('all');
                setOpenTaskId(null);
              }}
            >
              Current <span>{currentCount}</span>
            </button>
            <button
              className={taskScope === 'archived' ? 'active' : ''}
              role="tab"
              aria-selected={taskScope === 'archived'}
              onClick={() => {
                setTaskScope('archived');
                setColumnFilter('all');
                setOpenTaskId(null);
              }}
            >
              Archived <span>{archivedCount}</span>
            </button>
          </div>
          <div className="kb-view-switch" role="tablist" aria-label="Board layout">
            <button
              className={view === 'board' ? 'active' : ''}
              role="tab"
              aria-selected={view === 'board'}
              onClick={() => setView('board')}
            >
              <Columns3 size={14} /> Kanban
            </button>
            <button
              className={view === 'table' ? 'active' : ''}
              role="tab"
              aria-selected={view === 'table'}
              onClick={() => setView('table')}
            >
              <List size={14} /> List <span>{taskScope === 'current' ? currentCount : archivedCount}</span>
            </button>
          </div>
          {taskScope === 'current' && (
            <button className="primary-button" onClick={() => { setNewTaskStatus(undefined); setNewTaskOpen(true); }}>
              <Plus size={16} /> New task
            </button>
          )}
          <button className="icon-button" aria-label="Close board" onClick={onClose}>
            <X size={19} />
          </button>
        </div>
      </header>

      {state.notice && (
        <div className={`kb-notice ${state.notice.kind}`} role={state.notice.kind === 'error' ? 'alert' : 'status'}>
          {state.notice.kind === 'success' ? <Check size={15} /> : <AlertTriangle size={15} />}
          <span>{state.notice.message}</span>
          <button className="icon-button" aria-label="Dismiss notification" onClick={state.dismissNotice}>
            <X size={14} />
          </button>
        </div>
      )}

      <div className="kb-command">
        <label className="kb-search">
          <Search size={16} />
          <span className="sr-only">Search tasks</span>
          <input
            type="search"
            value={search}
            placeholder="Search tasks, IDs, tags, or agents…"
            onChange={(event) => setSearch(event.target.value)}
          />
          {search && (
            <button className="icon-button" type="button" aria-label="Clear search" onClick={() => setSearch('')}>
              <X size={14} />
            </button>
          )}
        </label>
        <div className="kb-filters">
          <label>
            <span className="sr-only">Filter by assignee</span>
            <select value={agentFilter} onChange={(event) => setAgentFilter(event.target.value)}>
              <option value="all">All agents</option>
              {assigneeOptions.map((person) => (
                <option key={person.id} value={person.id}>{person.name}</option>
              ))}
            </select>
          </label>
          <label>
            <span className="sr-only">Filter by priority</span>
            <select
              value={priorityFilter}
              onChange={(event) => setPriorityFilter(event.target.value as 'all' | KanbanPriority)}
            >
              <option value="all">All priorities</option>
              <option value="high">High priority</option>
              <option value="medium">Medium priority</option>
              <option value="low">Low priority</option>
            </select>
          </label>
          {taskScope === 'current' && (
            <label>
              <span className="sr-only">Filter by board column</span>
              <select
                value={columnFilter}
                onChange={(event) => setColumnFilter(event.target.value as 'all' | KanbanColumnId)}
              >
                <option value="all">All statuses</option>
                {KANBAN_COLUMNS.map((column) => (
                  <option key={column.id} value={column.id}>{column.label}</option>
                ))}
              </select>
            </label>
          )}
          {filtersActive && <button className="kb-clear" onClick={clearFilters}>Clear</button>}
        </div>
      </div>

      <details className="kb-live-feed">
        <summary>
          <span className={`kb-live-dot ${state.liveStatus}`} />
          Live task activity
          {state.events[0] && (
            <span className="kb-live-latest">
              {state.events[0].taskId} · {state.events[0].kind.replaceAll('_', ' ')}
            </span>
          )}
        </summary>
        <div className="kb-live-events">
          {state.events.length === 0 ? (
            <span className="kb-live-empty">Waiting for new task activity…</span>
          ) : state.events.slice(0, 8).map((event) => (
            <div className="kb-live-event" key={event.id}>
              <time>{eventTime(event.createdAt)}</time>
              <code>{event.taskId}</code>
              <strong>{event.kind.replaceAll('_', ' ')}</strong>
              <span>@{event.assignee || 'unassigned'}</span>
              <span className={`kb-substate native-${event.nativeStatus}`}>{nativeStatusLabel(event.nativeStatus)}</span>
            </div>
          ))}
        </div>
      </details>

      {state.status === 'loading' && <div className="kb-empty">Loading board…</div>}
      {state.status === 'error' && <div className="kb-error">{state.error}</div>}

      {board && state.status === 'ready' && taskScope === 'current' && (
        <div className="kb-summary" aria-label="Board summary">
          <div className="kb-metric">
            <span>Total work</span>
            <strong>{metrics.total}</strong>
            <small>tasks</small>
          </div>
          <div className="kb-metric active">
            <span>In Progress</span>
            <strong>{metrics.running}</strong>
            <small>{metrics.activeAgents} active {metrics.activeAgents === 1 ? 'agent' : 'agents'}</small>
          </div>
          <div className={`kb-metric ${metrics.blocked ? 'attention' : ''}`}>
            <span>Blocked</span>
            <strong>{metrics.blocked}</strong>
            <small>{metrics.blocked ? 'needs attention' : 'all clear'}</small>
          </div>
          <div className="kb-metric complete">
            <span>Completed</span>
            <strong>{metrics.completion}%</strong>
            <small>{metrics.completed} done</small>
          </div>
        </div>
      )}

      {board && state.status === 'ready' && taskScope === 'current' && view === 'board' && (
        <div className="kb-board">
          {KANBAN_COLUMNS.map((column) => {
            const items = filteredTasks.filter((task) => columnOf(task.status) === column.id);
            const total = board.tasks.filter((task) => columnOf(task.status) === column.id).length;
            const initialStatus = board.statuses.find((status) => status.column === column.id)?.id;
            return (
              <section
                key={column.id}
                aria-label={`${column.label} column`}
                className={`kb-column col-${column.id}${dropColumn === column.id ? ' drop-target' : ''}`}
                onDragEnter={(event) => {
                  event.preventDefault();
                  const dragged = board.tasks.find((task) => task.id === draggedTaskId);
                  if (dragged?.allowedStatuses.includes(column.id)) setDropColumn(column.id);
                }}
                onDragOver={(event) => {
                  event.preventDefault();
                  const dragged = board.tasks.find((task) => task.id === draggedTaskId);
                  event.dataTransfer.dropEffect = dragged?.allowedStatuses.includes(column.id) ? 'move' : 'none';
                }}
                onDragLeave={(event) => {
                  const related = event.relatedTarget;
                  if (!(related instanceof Node) || !event.currentTarget.contains(related)) {
                    setDropColumn((current) => current === column.id ? null : current);
                  }
                }}
                onDrop={(event) => {
                  event.preventDefault();
                  const taskId = draggedTaskId || event.dataTransfer.getData('text/plain');
                  setDraggedTaskId(null);
                  setDropColumn(null);
                  if (taskId) moveTask(taskId, column.id);
                }}
              >
                <header className="kb-column-head">
                  <span className="kb-swatch" />
                  <div>
                    <strong>{column.label}</strong>
                    <span>{column.hint}</span>
                  </div>
                  <span className="kb-count">{filtersActive ? `${items.length}/${total}` : total}</span>
                </header>
                <div className="kb-column-body">
                  {items.length === 0 ? (
                    <div className="kb-lane-empty">
                      <strong>{filtersActive ? 'No matching tasks' : 'No tasks yet'}</strong>
                      <span>{filtersActive ? 'Try changing your filters.' : `Add work to ${column.label.toLowerCase()}.`}</span>
                    </div>
                  ) : (
                    items.map((task) => (
                      <TaskCard
                        key={task.id}
                        task={task}
                        agents={agents}
                        statusLabel={statusLabel}
                        column={column.id}
                        onOpen={() => {
                          setOpenTaskId(task.id);
                          void state.refreshTask(task.id);
                        }}
                        onDragStart={() => setDraggedTaskId(task.id)}
                        onDragEnd={() => {
                          setDraggedTaskId(null);
                          setDropColumn(null);
                        }}
                      />
                    ))
                  )}
                  {(column.id === 'backlog' || column.id === 'todo') && (
                    <button
                      className="kb-column-add"
                      onClick={() => { setNewTaskStatus(initialStatus); setNewTaskOpen(true); }}
                    >
                      <Plus size={14} /> Add task
                    </button>
                  )}
                </div>
              </section>
            );
          })}
        </div>
      )}

      {board && state.status === 'ready' && taskScope === 'archived' && view === 'board' && (
        <div className="kb-archive-view">
          <header>
            <div>
              <span className="kb-swatch" />
              <div>
                <strong>{ARCHIVED_COLUMN.label}</strong>
                <span>{ARCHIVED_COLUMN.hint}</span>
              </div>
            </div>
            <span className="kb-count">{filteredTasks.length}</span>
          </header>
          {filteredTasks.length === 0 ? (
            <div className="kb-archive-empty">
              <Archive size={24} />
              <strong>No archived tasks</strong>
              <span>Archived work will appear here without crowding the current board.</span>
            </div>
          ) : (
            <div className="kb-archive-grid">
              {filteredTasks.map((task) => (
                <TaskCard
                  key={task.id}
                  task={task}
                  agents={agents}
                  statusLabel={statusLabel}
                  column="archived"
                  onOpen={() => {
                    setOpenTaskId(task.id);
                    void state.refreshTask(task.id);
                  }}
                  onDragStart={() => undefined}
                  onDragEnd={() => undefined}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {board && state.status === 'ready' && view === 'table' && (
        <div className="kb-table">
          <div className="kb-table-toolbar">
            <span>
              <strong>{filteredTasks.length}</strong> {filteredTasks.length === 1 ? 'task' : 'tasks'} · grouped by status
            </span>
            <span className="kb-fixed-status-note">
              {taskScope === 'current' ? 'Four current stages' : 'Archived history'}
            </span>
          </div>
          {board.statuses.filter((status) =>
            taskScope === 'archived' ? status.id === 'archived' : status.id !== 'archived'
          ).map((status) => {
            const items = filteredTasks.filter((task) => task.status === status.id);
            return (
              <div key={status.id} className="kb-group">
                <div className="kb-group-head">
                  <span className={`kb-status-dot col-${status.column}`} />
                  <strong>{status.label}</strong>
                  <span className="kb-count">{items.length}</span>
                  {(status.id === 'backlog' || status.id === 'todo') && <button
                    className="kb-group-add"
                    aria-label={`Add task to ${status.label}`}
                    onClick={() => { setNewTaskStatus(status.id); setNewTaskOpen(true); }}
                  >
                    <Plus size={14} />
                  </button>}
                </div>
                {items.length > 0 && (
                  <div className="kb-rows">
                    <div className="kb-row kb-row-labels" aria-hidden="true">
                      <span>Task</span>
                      <span>Assignee</span>
                      <span>Deps</span>
                      <span>Updated</span>
                      <span />
                    </div>
                    {items.map((task) => (
                      <button key={task.id} className="kb-row" onClick={() => {
                        setOpenTaskId(task.id);
                        void state.refreshTask(task.id);
                      }}>
                        <span className="kb-row-task">
                          <PriorityTitle task={task} />
                          <span className="kb-row-id">{task.id}</span>
                        </span>
                        <span className="kb-row-assignees">
                          <AssigneeSummary ids={task.assignees} agents={agents} />
                        </span>
                        <span className="kb-row-deps">
                          {task.deps.length ? (
                            <>
                              <Link2 size={12} /> {task.deps.filter((dep) => dep.state === 'done').length}/{task.deps.length}
                            </>
                          ) : (
                            '—'
                          )}
                        </span>
                        <span className="kb-row-updated">{task.updated}</span>
                        <ChevronRight size={15} className="kb-row-arrow" />
                      </button>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {openTask && (
        <TaskDrawer
          task={openTask}
          agents={agents}
          state={state}
          onMove={moveTask}
          onArchive={setArchiveTask}
          onClose={() => setOpenTaskId(null)}
        />
      )}
      {newTaskOpen && <NewTaskModal teams={teams} agents={agents} state={state} initialStatus={newTaskStatus} onClose={() => setNewTaskOpen(false)} />}
      {archiveTask && (
        <ArchiveConfirm
          task={archiveTask}
          onCancel={() => setArchiveTask(null)}
          onConfirm={confirmArchive}
        />
      )}
    </section>
  );
}
