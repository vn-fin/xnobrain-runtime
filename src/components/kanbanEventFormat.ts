import type { KanbanTaskEvent } from '../types';

export type KanbanEventPresentation = {
  title: string;
  description: string;
  details: string[];
  tone: 'neutral' | 'active' | 'success' | 'warning' | 'danger';
};

type AgentName = (agentId: string) => string;

const TITLES: Record<string, string> = {
  archived: 'Task archived',
  assigned: 'Assignee changed',
  attached: 'File attached',
  attachment_removed: 'File removed',
  block_loop_detected: 'Repeated blocker detected',
  claim_extended: 'Worker reservation extended',
  claim_rejected: 'Worker could not start',
  commented: 'Comment added',
  completed: 'Task completed',
  crashed: 'Worker stopped unexpectedly',
  created: 'Task created',
  decomposed: 'Task divided into subtasks',
  dependency_wait: 'Waiting for another task',
  edited: 'Task updated',
  gave_up: 'Automatic retries stopped',
  heartbeat: 'Worker check-in',
  linked: 'Dependency added',
  model_override_set: 'Model preference updated',
  promoted: 'Ready to run',
  promoted_manual: 'Manually moved to Ready',
  protocol_violation: 'Worker response was invalid',
  rate_limited: 'Provider rate limit reached',
  reclaimed: 'Worker reservation recovered',
  reclaim_deferred: 'Worker shutdown pending',
  respawn_guarded: 'Automatic restart paused',
  schedule_fired: 'Scheduled run started',
  schedule_paused: 'Schedule paused',
  schedule_resumed: 'Schedule resumed',
  schedule_set: 'Schedule configured',
  scheduled: 'Task scheduled',
  spawned: 'Worker started',
  specified: 'Task prepared',
  stale: 'Worker became unresponsive',
  timed_out: 'Execution timed out',
  tip_scratch_workspace: 'Workspace recommendation',
  unblocked: 'Task unblocked',
  unlinked: 'Dependency removed',
  workspace_changed: 'Workspace changed',
};

const STATUS_LABELS: Record<string, string> = {
  triage: 'Backlog',
  todo: 'Todo',
  ready: 'Ready',
  running: 'In Progress',
  blocked: 'Blocked',
  done: 'Done',
  archived: 'Archived',
  scheduled: 'Scheduled',
};

const BLOCK_LABELS: Record<string, string> = {
  capability: 'Missing capability',
  dependency: 'Dependency',
  needs_input: 'Needs input',
  transient: 'Temporary issue',
};

const WORKSPACE_LABELS: Record<string, string> = {
  dir: 'Uses the selected project folder',
  scratch: 'Uses a temporary workspace',
  worktree: 'Uses an isolated Git worktree',
};

function titleCase(value: string): string {
  return value
    .replaceAll('_', ' ')
    .replaceAll('-', ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function stringValue(payload: Record<string, unknown>, key: string): string {
  const value = payload[key];
  return typeof value === 'string' ? value.trim() : '';
}

function numberValue(payload: Record<string, unknown>, key: string): number | undefined {
  const value = payload[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

function stringList(payload: Record<string, unknown>, key: string): string[] {
  const value = payload[key];
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
}

function unixTime(value: number | undefined): string {
  if (value === undefined) return '';
  const date = new Date(value * 1000);
  return Number.isNaN(date.getTime())
    ? ''
    : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function workerHost(lock: string): string {
  return lock.split(':')[0]?.trim() ?? '';
}

function defaultTone(kind: string): KanbanEventPresentation['tone'] {
  if (['completed', 'promoted', 'promoted_manual', 'unblocked'].includes(kind)) return 'success';
  if (['claimed', 'heartbeat', 'spawned', 'schedule_fired'].includes(kind)) return 'active';
  if (['blocked', 'block_loop_detected', 'dependency_wait', 'rate_limited', 'scheduled', 'stale'].includes(kind)) return 'warning';
  if (['crashed', 'gave_up', 'protocol_violation', 'spawn_failed', 'timed_out'].includes(kind)) return 'danger';
  return 'neutral';
}

function genericDetails(payload: Record<string, unknown>): string[] {
  const ignored = new Set([
    'branch_name',
    'claim_expires',
    'claim_expires_now',
    'claim_expires_was',
    'claim_lock',
    'lock',
    'parents',
    'pid',
    'project_id',
    'skills',
    'tenant',
    'workspace_path',
  ]);
  return Object.entries(payload).flatMap(([key, value]) => {
    if (ignored.has(key) || value == null || value === '') return [];
    if (typeof value === 'boolean') return [`${titleCase(key)}: ${value ? 'Yes' : 'No'}`];
    if (typeof value === 'string' || typeof value === 'number') return [`${titleCase(key)}: ${String(value)}`];
    if (Array.isArray(value)) return value.length ? [`${titleCase(key)}: ${value.length}`] : [];
    return [];
  }).slice(0, 3);
}

export function formatKanbanEvent(
  event: KanbanTaskEvent,
  agentName: AgentName = (agentId) => titleCase(agentId),
): KanbanEventPresentation {
  const payload = event.payload ?? {};
  const title = TITLES[event.kind] ?? titleCase(event.kind);
  const tone = defaultTone(event.kind);
  const reason = stringValue(payload, 'reason') || stringValue(payload, 'error');
  const runId = numberValue(payload, 'run_id');

  switch (event.kind) {
    case 'created': {
      const assignee = stringValue(payload, 'assignee');
      const status = stringValue(payload, 'status');
      const skills = stringList(payload, 'skills');
      const parents = stringList(payload, 'parents');
      const workspace = stringValue(payload, 'workspace_kind');
      const description = [
        status ? `Created in ${STATUS_LABELS[status] ?? titleCase(status)}` : 'Created',
        assignee ? `assigned to ${agentName(assignee)}` : 'unassigned',
      ].join(' and ') + '.';
      const details = [
        ...(workspace && WORKSPACE_LABELS[workspace] ? [WORKSPACE_LABELS[workspace]] : []),
        ...(skills.length ? [`${skills.length.toLocaleString()} skills available`] : []),
        ...(parents.length ? [`Depends on ${parents.length.toLocaleString()} ${parents.length === 1 ? 'task' : 'tasks'}`] : []),
      ];
      return { title, description, details, tone };
    }
    case 'blocked': {
      const kind = stringValue(payload, 'kind');
      const recurrences = numberValue(payload, 'recurrences');
      return {
        title: BLOCK_LABELS[kind] ?? 'Task blocked',
        description: reason || 'The worker needs attention before it can continue.',
        details: [
          ...(kind ? [`Block type: ${BLOCK_LABELS[kind] ?? titleCase(kind)}`] : []),
          ...(recurrences ? [`Occurrence ${recurrences.toLocaleString()}`] : []),
        ],
        tone: 'warning',
      };
    }
    case 'heartbeat':
      return {
        title,
        description: stringValue(payload, 'note') || 'The worker reported that it is still active.',
        details: [],
        tone,
      };
    case 'spawned':
      return { title, description: 'The worker process started successfully.', details: [], tone };
    case 'claimed': {
      const host = workerHost(stringValue(payload, 'lock'));
      const expires = unixTime(numberValue(payload, 'expires'));
      return {
        title: 'Worker reserved task',
        description: `${runId ? `Run ${runId.toLocaleString()}` : 'A new run'} was claimed${host ? ` on ${host}` : ''}.`,
        details: expires ? [`Reservation expires at ${expires}`] : [],
        tone,
      };
    }
    case 'promoted':
      return { title, description: 'The task is ready for an available worker.', details: [], tone };
    case 'specified': {
      const fields = stringList(payload, 'changed_fields');
      return {
        title,
        description: 'The task requirements were finalized and queued.',
        details: fields.length ? [`Updated ${fields.map(titleCase).join(', ')}`] : [],
        tone,
      };
    }
    case 'assigned': {
      const assignee = stringValue(payload, 'assignee');
      return {
        title,
        description: assignee ? `Assigned to ${agentName(assignee)}.` : 'The task is now unassigned.',
        details: [],
        tone,
      };
    }
    case 'completed':
      return {
        title,
        description: stringValue(payload, 'summary') || 'The worker completed this task successfully.',
        details: stringList(payload, 'artifacts').length
          ? [`${stringList(payload, 'artifacts').length.toLocaleString()} artifacts created`]
          : [],
        tone,
      };
    case 'commented': {
      const author = stringValue(payload, 'author');
      return { title, description: author ? `${titleCase(author)} added context to the task.` : 'New context was added to the task.', details: [], tone };
    }
    case 'edited': {
      const fields = stringList(payload, 'fields');
      return {
        title,
        description: fields.length ? `Updated ${fields.map(titleCase).join(', ')}.` : 'The task details were updated.',
        details: [],
        tone,
      };
    }
    case 'workspace_changed': {
      const workspace = stringValue(payload, 'workspace_kind');
      return { title, description: WORKSPACE_LABELS[workspace] ?? 'The execution workspace was updated.', details: [], tone };
    }
    case 'linked':
    case 'unlinked': {
      const parent = stringValue(payload, 'parent');
      return {
        title,
        description: parent
          ? `${event.kind === 'linked' ? 'Now depends on' : 'No longer depends on'} task ${parent}.`
          : `The task dependency was ${event.kind === 'linked' ? 'added' : 'removed'}.`,
        details: [],
        tone,
      };
    }
    case 'scheduled':
    case 'dependency_wait':
    case 'crashed':
    case 'gave_up':
    case 'protocol_violation':
    case 'rate_limited':
    case 'respawn_guarded':
    case 'stale':
    case 'timed_out':
      return {
        title,
        description: reason || ({
          scheduled: 'The task will resume at its scheduled time.',
          dependency_wait: 'This task will continue when its dependencies finish.',
          crashed: 'The worker stopped before completing the task.',
          gave_up: 'The retry limit was reached and human attention is required.',
          protocol_violation: 'The worker returned a response the task runner could not accept.',
          rate_limited: 'The provider temporarily prevented another request.',
          respawn_guarded: 'The system paused another restart to prevent a retry loop.',
          stale: 'The worker stopped reporting progress and its reservation was recovered.',
          timed_out: 'The worker exceeded the allowed execution time.',
        }[event.kind] ?? 'The task state changed.'),
        details: genericDetails(payload),
        tone,
      };
    default:
      return {
        title,
        description: reason || 'The task lifecycle was updated.',
        details: genericDetails(payload),
        tone,
      };
  }
}
