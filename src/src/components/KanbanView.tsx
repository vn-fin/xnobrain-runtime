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

/** Priority is merged into the title as a leading colored bar (per the design). */
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
}: {
  task: KanbanTask;
  agents: Agent[];
  statusLabel: (status: string) => string;
  column: KanbanColumnId;
  onOpen: () => void;
}) {
  return (
    <button className={`kb-card col-${column}`} onClick={onOpen}>
      <div className="kb-card-top">
        <span className="kb-id">{task.id}</span>
        {column === 'done' && <span className={`kb-substate ${task.status}`}>{statusLabel(task.status)}</span>}
      </div>
      <PriorityTitle task={task} />
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
      ) : column === 'in_progress' ? (
        <div className="kb-progress">
          <div className="kb-progress-label">
            <span>
              <Clock size={12} /> In progress
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
        <AssigneeStack ids={task.assignees} agents={agents} />
        <span className="kb-updated">{task.updated}</span>
      </div>
    </button>
  );
}

function TaskDrawer({
  task,
  agents,
  state,
  onClose,
}: {
  task: KanbanTask;
  agents: Agent[];
  state: KanbanState;
  onClose: () => void;
}) {
  const board = state.board;
  if (!board) return null;
  return (
    <div className="kb-overlay" onClick={onClose}>
      <div className="kb-drawer" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
        <div className="kb-drawer-head">
          <div>
            <span className="kb-id">{task.id} · {state.statusLabel(task.status)}</span>
            <h2>{task.title}</h2>
          </div>
          <button className="icon-button" aria-label="Close" onClick={onClose}>
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
            <span className="kb-label">Assignees</span>
            <div className="kb-drawer-assignees">
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
            </div>
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
          <span className="kb-label">Move to status</span>
          <div className="kb-move-buttons">
            {board.statuses.map((status) => (
              <button
                key={status.id}
                className={status.id === task.status ? 'kb-move active' : 'kb-move'}
                disabled={status.id === task.status}
                onClick={() => state.moveTask(task.id, status.id)}
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
  const [assignees, setAssignees] = useState<string[]>([]);
  const [invalid, setInvalid] = useState(false);

  if (!board) return null;

  const toggleAssignee = (id: string) =>
    setAssignees((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]));

  const submit = async () => {
    if (!title.trim()) {
      setInvalid(true);
      return;
    }
    await state.createTask({ title: title.trim(), description: description.trim(), status, priority, assignees });
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
                {board.statuses.map((entry) => (
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
            Assignees
            <div className="kb-assignee-picker">
              {agents.length === 0 && <span className="kb-unassigned">No agents available</span>}
              {agents.map((agent) => (
                <button
                  key={agent.id}
                  className={assignees.includes(agent.id) ? 'kb-assignee-chip on' : 'kb-assignee-chip'}
                  onClick={() => toggleAssignee(agent.id)}
                >
                  <span className="kb-avatar" style={{ background: colorFor(agent.id) }}>{monogram(agent.title)}</span>
                  {agent.title}
                </button>
              ))}
            </div>
          </div>
          <label className="kb-field">
            Description
            <textarea
              value={description}
              placeholder="Optional context for the assignees…"
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
  const [addingStatus, setAddingStatus] = useState(false);
  const [statusLabelInput, setStatusLabelInput] = useState('');
  const [statusColumn, setStatusColumn] = useState<KanbanColumnId>('todo');

  const openTask = useMemo(
    () => (openTaskId ? visibleTasks.find((task) => task.id === openTaskId) ?? null : null),
    [openTaskId, visibleTasks],
  );

  const submitStatus = async () => {
    if (!statusLabelInput.trim()) return;
    await state.addStatus(statusLabelInput.trim(), statusColumn);
    setStatusLabelInput('');
    setAddingStatus(false);
  };

  return (
    <section className="kanban-view">
      <header className="kb-header">
        <div className="kb-title-group">
          <h1>
            <Columns3 size={22} /> {board?.name ?? 'Task board'}
          </h1>
          <span className="kb-sub">{board?.description}</span>
        </div>
        <div className="kb-header-actions">
          <span className="kb-proto" title="This board uses in-memory sample data">Sample data</span>
          <div className="kb-view-switch" role="tablist" aria-label="Board layout">
            <button
              className={view === 'board' ? 'active' : ''}
              aria-selected={view === 'board'}
              onClick={() => setView('board')}
            >
              <Columns3 size={14} /> Kanban
            </button>
            <button
              className={view === 'table' ? 'active' : ''}
              aria-selected={view === 'table'}
              onClick={() => setView('table')}
            >
              <List size={14} /> Table
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

      <div className="kb-command">
        <div className="kb-search">
          <Search size={16} />
          <input
            value={search}
            placeholder="Search tasks, ids, tags, assignees…"
            onChange={(event) => setSearch(event.target.value)}
          />
          {search && (
            <button className="icon-button" aria-label="Clear search" onClick={() => setSearch('')}>
              <X size={14} />
            </button>
          )}
        </div>
      </div>

      {state.status === 'loading' && <div className="kb-empty">Loading board…</div>}
      {state.status === 'error' && <div className="kb-error">{state.error}</div>}

      {board && state.status === 'ready' && view === 'board' && (
        <div className="kb-board">
          {KANBAN_COLUMNS.map((column) => {
            const items = visibleTasks.filter((task) => columnOf(task.status) === column.id);
            return (
              <section key={column.id} className={`kb-column col-${column.id}`}>
                <header className="kb-column-head">
                  <span className="kb-swatch" />
                  <strong>{column.label}</strong>
                  <span className="kb-count">{items.length}</span>
                </header>
                <div className="kb-column-body">
                  <span className="kb-column-hint">{column.hint}</span>
                  {items.length === 0 ? (
                    <div className="kb-lane-empty">No tasks</div>
                  ) : (
                    items.map((task) => (
                      <TaskCard
                        key={task.id}
                        task={task}
                        agents={agents}
                        statusLabel={statusLabel}
                        column={column.id}
                        onOpen={() => setOpenTaskId(task.id)}
                      />
                    ))
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
            <span>{visibleTasks.length} tasks · grouped by status</span>
            {addingStatus ? (
              <div className="kb-add-status">
                <input
                  value={statusLabelInput}
                  autoFocus
                  placeholder="New status name"
                  onChange={(event) => setStatusLabelInput(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') void submitStatus();
                    if (event.key === 'Escape') setAddingStatus(false);
                  }}
                />
                <select value={statusColumn} onChange={(event) => setStatusColumn(event.target.value as KanbanColumnId)}>
                  {KANBAN_COLUMNS.map((column) => (
                    <option key={column.id} value={column.id}>{column.label}</option>
                  ))}
                </select>
                <button className="conn-btn ghost" onClick={() => void submitStatus()}>Add</button>
                <button className="icon-button" aria-label="Cancel" onClick={() => setAddingStatus(false)}>
                  <X size={15} />
                </button>
              </div>
            ) : (
              <button className="conn-btn ghost" onClick={() => setAddingStatus(true)}>
                <Plus size={15} /> New status
              </button>
            )}
          </div>
          {board.statuses.map((status) => {
            const items = visibleTasks.filter((task) => task.status === status.id);
            return (
              <div key={status.id} className="kb-group">
                <div className="kb-group-head">
                  <span className={`kb-status-dot col-${status.column}`} />
                  <strong>{status.label}</strong>
                  <span className="kb-count">{items.length}</span>
                  <button
                    className="kb-group-add"
                    aria-label={`Add task to ${status.label}`}
                    onClick={() => { setNewTaskStatus(status.id); setNewTaskOpen(true); }}
                  >
                    <Plus size={14} />
                  </button>
                </div>
                {items.length > 0 && (
                  <div className="kb-rows">
                    {items.map((task) => (
                      <button key={task.id} className="kb-row" onClick={() => setOpenTaskId(task.id)}>
                        <span className="kb-row-task">
                          <PriorityTitle task={task} />
                          <span className="kb-row-id">{task.id}</span>
                        </span>
                        <span className="kb-row-assignees">
                          <AssigneeStack ids={task.assignees} agents={agents} />
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
        <TaskDrawer task={openTask} agents={agents} state={state} onClose={() => setOpenTaskId(null)} />
      )}
      {newTaskOpen && <NewTaskModal agents={agents} state={state} initialStatus={newTaskStatus} onClose={() => setNewTaskOpen(false)} />}
    </section>
  );
}
