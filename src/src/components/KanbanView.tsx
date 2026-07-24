import { useMemo, useState } from 'react';
import {
  AlertTriangle,
  Check,
  ChevronRight,
  Clock,
  Columns3,
  Link2,
  List,
  Plus,
  Search,
  X,
} from 'lucide-react';
import { KANBAN_COLUMNS } from '../api/kanban';
import type { useKanban } from '../hooks/useKanban';
import type { Agent, KanbanColumnId, KanbanPriority, KanbanTask } from '../types';

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
  return status.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function eventTime(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
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
  return (
    <button
      className={`kb-card col-${column} status-${task.nativeStatus}`}
      draggable
      onDragStart={(event) => {
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
      <p className="kb-card-desc">{task.description}</p>
      {task.tags.length > 0 && (
        <div className="kb-tags">
          {task.tags.map((tag) => (
            <span key={tag} className="kb-tag">#{tag}</span>
          ))}
        </div>
      )}
      {task.block ? (
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

function TaskDrawer({
  task,
  agents,
  state,
  onMove,
  onClose,
}: {
  task: KanbanTask;
  agents: Agent[];
  state: KanbanState;
  onMove: (taskId: string, status: KanbanColumnId) => void;
  onClose: () => void;
}) {
  const board = state.board;
  if (!board) return null;
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
          <button className="icon-button" aria-label="Close task details" onClick={onClose}>
            <X size={18} />
          </button>
        </div>
        <div className="kb-drawer-body">
          <section>
            <span className="kb-label">Overview</span>
            <p className="kb-desc">{task.description}</p>
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

          <section>
            <span className="kb-label">Assignee</span>
            <div className="kb-drawer-assignees">
              {['triage', 'todo', 'ready', 'scheduled'].includes(task.nativeStatus) ? (
                <select
                  className="kb-assignee-select"
                  aria-label="Change task assignee"
                  value={task.assignees[0] ?? ''}
                  onChange={(event) => void state.assignTask(task.id, event.target.value || null)}
                >
                  <option value="">Unassigned</option>
                  {agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.title}</option>)}
                </select>
              ) : (
                <>
                  {task.assignees.length === 0 && <span className="kb-unassigned">Unassigned</span>}
                  {task.assignees.map((id) => {
                    const person = resolveAssignee(id, agents);
                    return (
                      <span key={id} className="kb-person">
                        <span className="kb-avatar" style={{ background: person.color }}>{monogram(person.name)}</span>
                        {person.name}
                      </span>
                    );
                  })}
                </>
              )}
            </div>
            {['triage', 'todo', 'ready', 'scheduled'].includes(task.nativeStatus) && (
              <small className="kb-field-help">Choose the single agent that will run this task.</small>
            )}
          </section>

          <section className="kb-drawer-grid">
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

          {task.deps.length > 0 && (
            <section>
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
        </div>
        <div className="kb-drawer-foot">
          <div>
            <span className="kb-label">Move task</span>
            <p>Update the task status without leaving the board.</p>
          </div>
          <div className="kb-move-buttons">
            {board.statuses.map((status) => (
              <button
                key={status.id}
                className={status.id === task.status ? 'kb-move active' : 'kb-move'}
                disabled={status.id === task.status}
                onClick={() => onMove(task.id, status.id)}
              >
                {status.label}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function NewTaskModal({
  agents,
  state,
  initialStatus,
  onClose,
}: {
  agents: Agent[];
  state: KanbanState;
  initialStatus?: string;
  onClose: () => void;
}) {
  const board = state.board;
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [status, setStatus] = useState(initialStatus ?? board?.statuses[0]?.id ?? 'todo');
  const [priority, setPriority] = useState<KanbanPriority>('medium');
  const [assignee, setAssignee] = useState('');
  const [invalid, setInvalid] = useState(false);

  if (!board) return null;

  const toggleAssignee = (id: string) => setAssignee((current) => current === id ? '' : id);

  const submit = async () => {
    if (!title.trim()) {
      setInvalid(true);
      return;
    }
    await state.createTask({ title: title.trim(), description: description.trim(), status: status as KanbanColumnId, priority, assignee: assignee || null });
    onClose();
  };

  return (
    <div className="kb-overlay" onClick={onClose}>
      <div className="kb-modal" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
        <div className="kb-modal-head">
          <h2>New task</h2>
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
              <select value={status} onChange={(event) => setStatus(event.target.value)}>
                {board.statuses.filter((entry) => entry.id === 'backlog' || entry.id === 'todo').map((entry) => (
                  <option key={entry.id} value={entry.id}>{entry.label}</option>
                ))}
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
            Assignee
            <div className="kb-assignee-picker">
              {agents.length === 0 && <span className="kb-unassigned">No agents available</span>}
              {agents.map((agent) => (
                <button
                  key={agent.id}
                  className={assignee === agent.id ? 'kb-assignee-chip on' : 'kb-assignee-chip'}
                  onClick={() => toggleAssignee(agent.id)}
                >
                  <span className="kb-avatar" style={{ background: colorFor(agent.id) }}>{monogram(agent.title)}</span>
                  {agent.title}
                </button>
              ))}
            </div>
            <small className="kb-field-help">One agent owns and runs each task.</small>
          </div>
          <label className="kb-field">
            Description
            <textarea
              value={description}
              placeholder="Optional context for the assignee…"
              onChange={(event) => setDescription(event.target.value)}
            />
          </label>
        </div>
        <div className="kb-modal-foot">
          <button className="conn-btn ghost" onClick={onClose}>Cancel</button>
          <button className="primary-button" onClick={() => void submit()}>
            <Plus size={16} /> Create task
          </button>
        </div>
      </div>
    </div>
  );
}

export function KanbanView({
  agents,
  state,
  onClose,
}: {
  agents: Agent[];
  state: KanbanState;
  onClose: () => void;
}) {
  const { board, view, setView, search, setSearch, visibleTasks, columnOf, statusLabel } = state;
  const [openTaskId, setOpenTaskId] = useState<string | null>(null);
  const [newTaskOpen, setNewTaskOpen] = useState(false);
  const [newTaskStatus, setNewTaskStatus] = useState<string | undefined>(undefined);
  const [agentFilter, setAgentFilter] = useState('all');
  const [priorityFilter, setPriorityFilter] = useState<'all' | KanbanPriority>('all');
  const [columnFilter, setColumnFilter] = useState<'all' | KanbanColumnId>('all');
  const [draggedTaskId, setDraggedTaskId] = useState<string | null>(null);
  const [dropColumn, setDropColumn] = useState<KanbanColumnId | null>(null);

  const openTask = useMemo(
    () => (openTaskId ? board?.tasks.find((task) => task.id === openTaskId) ?? null : null),
    [board, openTaskId],
  );

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
          (agentFilter === 'all' || task.assignees.includes(agentFilter)) &&
          (priorityFilter === 'all' || task.priority === priorityFilter) &&
          (columnFilter === 'all' || columnOf(task.status) === columnFilter),
      ),
    [agentFilter, columnFilter, columnOf, priorityFilter, visibleTasks],
  );

  const metrics = useMemo(() => {
    const tasks = board?.tasks ?? [];
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
    if (
      status === 'archived'
      && !window.confirm(`Archive “${task.title}”? You can still find it in the Archived column.`)
    ) {
      return;
    }
    void state.moveTask(taskId, status);
  };

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
              <List size={14} /> List <span>{board?.tasks.length ?? 0}</span>
            </button>
          </div>
          <button className="primary-button" onClick={() => { setNewTaskStatus(undefined); setNewTaskOpen(true); }}>
            <Plus size={16} /> New task
          </button>
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

      {board && state.status === 'ready' && (
        <div className="kb-summary" aria-label="Board summary">
          <div className="kb-metric">
            <span>Total work</span>
            <strong>{metrics.total}</strong>
            <small>tasks</small>
          </div>
          <div className="kb-metric active">
            <span>Running</span>
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

      {board && state.status === 'ready' && view === 'board' && (
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
                  if (draggedTaskId) setDropColumn(column.id);
                }}
                onDragOver={(event) => {
                  event.preventDefault();
                  event.dataTransfer.dropEffect = 'move';
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
                        onOpen={() => setOpenTaskId(task.id)}
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

      {board && state.status === 'ready' && view === 'table' && (
        <div className="kb-table">
          <div className="kb-table-toolbar">
            <span>
              <strong>{filteredTasks.length}</strong> {filteredTasks.length === 1 ? 'task' : 'tasks'} · grouped by status
            </span>
            <span className="kb-fixed-status-note">Five default statuses</span>
          </div>
          {board.statuses.map((status) => {
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
                      <button key={task.id} className="kb-row" onClick={() => setOpenTaskId(task.id)}>
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
          onClose={() => setOpenTaskId(null)}
        />
      )}
      {newTaskOpen && <NewTaskModal agents={agents} state={state} initialStatus={newTaskStatus} onClose={() => setNewTaskOpen(false)} />}
    </section>
  );
}
