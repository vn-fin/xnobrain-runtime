import type {
  KanbanBoard,
  KanbanStatusDef,
  KanbanTask,
  NewKanbanTaskInput,
} from '../types';

// ---------------------------------------------------------------------------
// Smoke API for the Kanban board.
//
// This is intentionally backed by in-memory sample data so the board renders
// end-to-end without a running backend. Every method returns a Promise and the
// shape mirrors what a real `/agent-gateway/v1/kanban` service would return, so
// swapping these implementations for `request(...)` calls later is mechanical.
//
// Assignees are referenced by agent id and resolved against the app's real
// agents in the UI layer — the board is "a new group over the assistants".
// ---------------------------------------------------------------------------

/** The three board columns every board collapses its statuses into. */
export const KANBAN_COLUMNS: Array<{ id: 'todo' | 'in_progress' | 'done'; label: string; hint: string }> = [
  { id: 'todo', label: 'Todo', hint: 'Queued & scheduled work' },
  { id: 'in_progress', label: 'In progress', hint: 'Being worked on now' },
  { id: 'done', label: 'Done', hint: 'Done · reviewed · blocked' },
];

/** Default fine-grained status vocabulary, merged into the three columns. */
const DEFAULT_STATUSES: KanbanStatusDef[] = [
  { id: 'todo', label: 'Todo', column: 'todo' },
  { id: 'scheduled', label: 'Scheduled', column: 'todo' },
  { id: 'in_progress', label: 'In progress', column: 'in_progress' },
  { id: 'done', label: 'Done', column: 'done' },
  { id: 'reviewed', label: 'Reviewed', column: 'done' },
  { id: 'blocked', label: 'Blocked', column: 'done' },
];

const SEED_BOARD: KanbanBoard = {
  id: 'default',
  name: 'Task board',
  description: 'Shared multi-agent work queue',
  color: '#4f8cff',
  statuses: DEFAULT_STATUSES.map((status) => ({ ...status })),
  tasks: [
    {
      id: 'T-1042',
      title: 'Design multi-agent board shell',
      description: 'Build the board layout: three merged columns plus a table view that groups by status.',
      status: 'in_progress',
      priority: 'high',
      assignees: ['ui-builder', 'research-agent'],
      tags: ['ui', 'dashboard'],
      deps: [{ id: 'T-1010', title: 'Approve palette tokens', state: 'done' }],
      progress: 62,
      updated: '4m ago',
    },
    {
      id: 'T-1043',
      title: 'Map task API response fields',
      description: 'Read the gateway schema and map task/assignee/dependency shapes to the board.',
      status: 'in_progress',
      priority: 'medium',
      assignees: ['api-mapper'],
      tags: ['api', 'contract'],
      deps: [{ id: 'T-1002', title: 'Gateway auth handshake', state: 'done' }],
      progress: 35,
      updated: '2m ago',
    },
    {
      id: 'T-1050',
      title: 'Summarize provider connection docs',
      description: 'Read provider connect flows and prepare a one-page operator summary.',
      status: 'todo',
      priority: 'medium',
      assignees: ['research-agent'],
      tags: ['docs', 'providers'],
      deps: [],
      progress: 0,
      updated: '18m ago',
    },
    {
      id: 'T-1051',
      title: 'Draft cron digest report template',
      description: 'Produce a reusable template for the daily metrics digest scheduled job.',
      status: 'scheduled',
      priority: 'low',
      assignees: ['research-agent', 'sandbox-ops'],
      tags: ['cron', 'reporting'],
      deps: [{ id: 'T-1050', title: 'Provider docs summary', state: 'pending' }],
      progress: 0,
      updated: 'due in 18m',
    },
    {
      id: 'T-1053',
      title: 'Investigate workspace tree lag',
      description: 'Large files may cause the workspace tree to render slowly. Needs profiling.',
      status: 'todo',
      priority: 'medium',
      assignees: ['ui-builder'],
      tags: ['perf', 'workspace'],
      deps: [],
      progress: 0,
      updated: '1h ago',
    },
    {
      id: 'T-1048',
      title: 'Retry flaky sandbox smoke test',
      description: 'The smoke suite intermittently fails while provisioning the sandbox VM network.',
      status: 'blocked',
      priority: 'high',
      assignees: ['test-runner'],
      tags: ['qa', 'sandbox'],
      deps: [{ id: 'T-1041', title: 'Sandbox gateway healthy', state: 'blocked' }],
      progress: 0,
      block: 'Dependency T-1041 (sandbox gateway) is unhealthy — retries exhausted.',
      updated: '9m ago',
    },
    {
      id: 'T-1057',
      title: 'Approve sandbox recovery summary',
      description: 'Review the recovery summary before the incident loop is closed.',
      status: 'reviewed',
      priority: 'medium',
      assignees: ['test-runner', 'ui-builder'],
      tags: ['review', 'recovery'],
      deps: [{ id: 'T-1048', title: 'Retry flaky sandbox smoke test', state: 'pending' }],
      progress: 100,
      updated: '5m ago',
    },
    {
      id: 'T-1028',
      title: 'Wire skill toggle without page refresh',
      description: 'Toggle installed skills on/off and reflect state immediately without a full reload.',
      status: 'done',
      priority: 'high',
      assignees: ['ui-builder'],
      tags: ['skills', 'ui'],
      deps: [{ id: 'T-1005', title: 'Skill registry contract', state: 'done' }],
      progress: 100,
      summary: 'Merged. Toggle updates the in-memory store and refreshes the affected agent instantly.',
      updated: '18m ago',
    },
    {
      id: 'T-1035',
      title: 'Stream run steps to chat',
      description: 'Render tool/run steps incrementally as the gateway streams events.',
      status: 'done',
      priority: 'medium',
      assignees: ['api-mapper'],
      tags: ['stream', 'chat'],
      deps: [],
      progress: 100,
      summary: 'Completed after one retry. Steps render in order with tool badges.',
      updated: '41m ago',
    },
  ],
};

const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T;

// A single mutable in-memory board. Refreshing the page resets it.
let board: KanbanBoard = clone(SEED_BOARD);
let nextId = 1100;

const delay = <T,>(value: T, ms = 120): Promise<T> =>
  new Promise((resolve) => setTimeout(() => resolve(clone(value)), ms));

export const kanbanApi = {
  /** Load the board with its statuses and tasks. */
  getBoard: (): Promise<KanbanBoard> => delay(board),

  /** Create a task in a given status. */
  createTask: (input: NewKanbanTaskInput): Promise<KanbanTask> => {
    const task: KanbanTask = {
      id: `T-${nextId++}`,
      title: input.title,
      description: input.description || 'No description provided.',
      status: input.status,
      priority: input.priority,
      assignees: input.assignees,
      tags: ['new'],
      deps: [],
      progress: input.status === 'done' || input.status === 'reviewed' ? 100 : 0,
      updated: 'just now',
      block: input.status === 'blocked' ? 'Created directly in the Blocked status.' : null,
      summary: input.status === 'done' ? 'Created directly in Done.' : null,
    };
    board.tasks = [task, ...board.tasks];
    return delay(task);
  },

  /** Move a task to a different fine-grained status. */
  moveTask: (taskId: string, status: string): Promise<KanbanTask | null> => {
    const task = board.tasks.find((item) => item.id === taskId);
    if (!task) return delay(null);
    task.status = status;
    task.updated = 'just now';
    if (status === 'done' || status === 'reviewed') task.progress = 100;
    if (status === 'blocked' && !task.block) task.block = 'Manually flagged as blocked.';
    if (status === 'done' && !task.summary) task.summary = 'Marked complete from the board.';
    return delay(task);
  },

  /** Register a new custom status (adds a column-group in the table view). */
  addStatus: (label: string, column: KanbanStatusDef['column']): Promise<KanbanStatusDef> => {
    const id = label
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '') || `status-${board.statuses.length + 1}`;
    const existing = board.statuses.find((status) => status.id === id);
    if (existing) return delay(existing);
    const status: KanbanStatusDef = { id, label: label.trim(), column };
    board.statuses = [...board.statuses, status];
    return delay(status);
  },
};
