import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react';
import {
  ArrowLeft,
  Bot,
  Brain,
  Check,
  ChevronDown,
  ChevronRight,
  Clock,
  Coins,
  Download,
  FileArchive,
  GitBranch,
  List,
  Loader2,
  Maximize2,
  MessageSquare,
  Minus,
  MoreHorizontal,
  MousePointer2,
  Network,
  Pencil,
  Play,
  Plus,
  Save,
  Send,
  Square,
  Trash2,
  Upload,
  X,
} from 'lucide-react';
import { conversationsApi } from '../api/conversations';
import { historicalRuns } from '../chat/runEvents';
import {
  isRunTerminal,
  type Team,
  type TeamInput,
  type TeamRunRecord,
  type TeamRunStep,
  type TeamWorkflowStep,
} from '../api/teams';
import type { Agent, ChatMessage, ConversationUsage } from '../types';
import type { useTeams } from '../hooks/useTeams';
import {
  systemApi,
  type BundleTransfer,
  type ImportReport,
  type TransferProgress,
} from '../features/system/api';
import { ConfirmDialog } from './modals';
import { SessionConversationModal, type SessionConversationFallback } from './SessionConversationModal';

type TeamsState = ReturnType<typeof useTeams>;
type TeamsMode = 'library' | 'builder';
type DraftNode = TeamWorkflowStep & { agent_id: string; role: string; x: number; y: number };

const CANVAS_WIDTH = 900;
const CANVAS_HEIGHT = 620;
const NODE_WIDTH = 220;
const NODE_HEIGHT = 118;
const TEAM_TOOLSETS = [
  ['web', 'Web search'],
  ['browser', 'Browser automation'],
  ['terminal', 'Terminal & processes'],
  ['file', 'File operations'],
  ['code_execution', 'Code execution'],
  ['skills', 'Skills'],
  ['vision', 'Vision'],
  ['image_gen', 'Image generation'],
  ['video', 'Video analysis'],
  ['video_gen', 'Video generation'],
  ['x_search', 'X search'],
  ['tts', 'Text to speech'],
  ['computer_use', 'Computer use'],
  ['session_search', 'Session search'],
  ['context_engine', 'Context engine'],
  ['todo', 'Task planning'],
] as const;
const ESSENTIAL_TEAM_TOOLSETS = [
  'web',
  'browser',
  'terminal',
  'file',
  'code_execution',
  'skills',
  'todo',
];
const START_STAGE_ID = '__start__';
const FINISH_STAGE_ID = '__finish__';
const DEFAULT_START_PROMPT = 'Plan the workflow and give every stage clear, actionable execution guidance.';
const DEFAULT_FINISH_PROMPT = 'Synthesize all completed stage outputs into one clear, accurate final answer.';
const COMMUNICATION_LEVELS = [
  { value: 0, label: 'Isolated', description: 'Dependencies control order; results are not shared.' },
  { value: 1, label: 'Result passing', description: 'Downstream stages receive upstream summaries.' },
  { value: 2, label: 'Shared scratchpad', description: 'Agents also collaborate through a run workspace.' },
  { value: 3, label: 'Team dialogue', description: 'Upstream agents review dependent drafts before completion.' },
] as const;

type RunGraphNode = { step: TeamRunStep; x: number; y: number };
type NodeConversationInsight = SessionConversationFallback & { reasoningSteps: number };
type RunGraphRecord = Pick<TeamRunRecord, 'status' | 'steps' | 'coordinator_conversation_id' | 'synthesis_conversation_id'>;

const insightRequests = new Map<string, Promise<NodeConversationInsight>>();

const RUN_NODE_WIDTH = 236;
const RUN_NODE_HEIGHT = 116;
const RUN_NODE_GAP_X = 280;
const RUN_NODE_GAP_Y = 140;
const RUN_START_X = 20;
const RUN_WORKER_START_X = RUN_START_X + RUN_NODE_WIDTH + 120;
const MIN_GRAPH_ZOOM = 0.35;
const MAX_GRAPH_ZOOM = 1.5;

function boundedZoom(value: number) {
  return Math.min(MAX_GRAPH_ZOOM, Math.max(MIN_GRAPH_ZOOM, Math.round(value * 100) / 100));
}

function GraphViewport({
  width,
  height,
  className,
  children,
  onArrange,
}: {
  width: number;
  height: number;
  className: string;
  children: ReactNode;
  onArrange?: () => void;
}) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const userZoomedRef = useRef(false);
  const panRef = useRef<{ pointerId: number; x: number; y: number; left: number; top: number }>();
  const [zoom, setZoom] = useState(1);
  const [panning, setPanning] = useState(false);

  const changeZoom = (requested: number, anchorX?: number, anchorY?: number) => {
    const viewport = viewportRef.current;
    const next = boundedZoom(requested);
    if (!viewport || next === zoom) return;
    const x = anchorX ?? viewport.clientWidth / 2;
    const y = anchorY ?? viewport.clientHeight / 2;
    const logicalX = (viewport.scrollLeft + x) / zoom;
    const logicalY = (viewport.scrollTop + y) / zoom;
    userZoomedRef.current = true;
    setZoom(next);
    requestAnimationFrame(() => {
      viewport.scrollLeft = logicalX * next - x;
      viewport.scrollTop = logicalY * next - y;
    });
  };

  const fit = () => {
    const viewport = viewportRef.current;
    if (!viewport?.clientWidth || !viewport.clientHeight) return;
    userZoomedRef.current = false;
    const next = boundedZoom(Math.min(1, (viewport.clientWidth - 32) / width, (viewport.clientHeight - 32) / height));
    setZoom(next);
    requestAnimationFrame(() => {
      viewport.scrollLeft = Math.max(0, (width * next - viewport.clientWidth) / 2);
      viewport.scrollTop = Math.max(0, (height * next - viewport.clientHeight) / 2);
    });
  };

  useEffect(() => {
    if (!userZoomedRef.current) requestAnimationFrame(fit);
  }, [width, height]);

  useEffect(() => {
    const fitOnResize = () => {
      if (!userZoomedRef.current) fit();
    };
    window.addEventListener('resize', fitOnResize);
    return () => window.removeEventListener('resize', fitOnResize);
  }, [width, height]);

  const handleWheel = (event: WheelEvent) => {
    event.preventDefault();
    const viewport = viewportRef.current;
    if (!viewport) return;
    const rect = viewport.getBoundingClientRect();
    changeZoom(zoom * Math.exp(-event.deltaY * 0.0015), event.clientX - rect.left, event.clientY - rect.top);
  };

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return undefined;
    viewport.addEventListener('wheel', handleWheel, { passive: false });
    return () => viewport.removeEventListener('wheel', handleWheel);
  }, [zoom]);

  const beginPan = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || (event.target as HTMLElement).closest('button, a, input, textarea, select, [role="button"]')) return;
    const viewport = viewportRef.current;
    if (!viewport) return;
    panRef.current = {
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      left: viewport.scrollLeft,
      top: viewport.scrollTop,
    };
    setPanning(true);
    if (typeof event.currentTarget.setPointerCapture === 'function') {
      event.currentTarget.setPointerCapture(event.pointerId);
    }
    event.preventDefault();
  };

  const movePan = (event: ReactPointerEvent<HTMLDivElement>) => {
    const pan = panRef.current;
    const viewport = viewportRef.current;
    if (!pan || !viewport || pan.pointerId !== event.pointerId) return;
    viewport.scrollLeft = pan.left - (event.clientX - pan.x);
    viewport.scrollTop = pan.top - (event.clientY - pan.y);
  };

  const endPan = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (panRef.current?.pointerId !== event.pointerId) return;
    panRef.current = undefined;
    setPanning(false);
    if (typeof event.currentTarget.hasPointerCapture === 'function'
      && event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };

  return (
    <div className={`graph-zoom-frame ${className}`}>
      <div className="graph-zoom-controls" aria-label="Workflow zoom controls">
        {onArrange && (
          <button type="button" onClick={onArrange} aria-label="Arrange workflow" title="Arrange workflow">
            <Network size={14} />
          </button>
        )}
        <button type="button" onClick={() => changeZoom(zoom - .1)} aria-label="Zoom out workflow" title="Zoom out">
          <Minus size={14} />
        </button>
        <button type="button" className="graph-zoom-value" onClick={fit} aria-label="Fit workflow to view" title="Fit to view">
          <Maximize2 size={13} /><span>{Math.round(zoom * 100)}%</span>
        </button>
        <button type="button" onClick={() => changeZoom(zoom + .1)} aria-label="Zoom in workflow" title="Zoom in">
          <Plus size={14} />
        </button>
      </div>
      <div
        ref={viewportRef}
        className={`graph-zoom-viewport${panning ? ' is-panning' : ''}`}
        onPointerDown={beginPan}
        onPointerMove={movePan}
        onPointerUp={endPan}
        onPointerCancel={endPan}
        title="Scroll to zoom · drag the background to pan"
      >
        <div className="graph-zoom-stage" style={{ width: width * zoom, height: height * zoom }}>
          <div className="graph-zoom-scene" style={{ width, height, transform: `translate(-50%, -50%) scale(${zoom})` }}>
            {children}
          </div>
        </div>
      </div>
    </div>
  );
}

function conversationInsight(messages: ChatMessage[], usage?: ConversationUsage): NodeConversationInsight {
  const runs = historicalRuns(messages);
  return {
    status: 'ready',
    messages,
    usage,
    runs,
    reasoningSteps: runs.reduce((total, run) => total + run.steps.length, 0),
  };
}

function loadConversationInsight(agentId: string, conversationId: string, revision: number) {
  const key = `${agentId}:${conversationId}:${revision}`;
  const pending = insightRequests.get(key);
  if (pending) return pending;
  const request: Promise<NodeConversationInsight> = Promise.allSettled([
    conversationsApi.messages(agentId, conversationId),
    conversationsApi.usage(agentId, conversationId),
  ]).then(([messagesResult, usageResult]): NodeConversationInsight => {
    if (messagesResult.status === 'rejected') {
      return {
        status: 'error',
        messages: [],
        runs: [],
        reasoningSteps: 0,
        error: messagesResult.reason instanceof Error ? messagesResult.reason.message : 'Could not load this session.',
      };
    }
    return conversationInsight(
      messagesResult.value,
      usageResult.status === 'fulfilled' ? usageResult.value : undefined,
    );
  }).finally(() => {
    if (insightRequests.get(key) === request) insightRequests.delete(key);
  });
  insightRequests.set(key, request);
  return request;
}

function compactNumber(value: number | undefined) {
  if (value === undefined) return '—';
  return new Intl.NumberFormat(undefined, { notation: value >= 1_000 ? 'compact' : 'standard', maximumFractionDigits: 1 }).format(value);
}

function runGraphLayout(steps: TeamRunStep[]) {
  const byId = new Map(steps.map((step) => [step.id, step]));
  const levels = new Map<string, number>();
  const levelOf = (id: string, visiting = new Set<string>()): number => {
    if (levels.has(id)) return levels.get(id) as number;
    if (visiting.has(id)) return 0;
    visiting.add(id);
    const step = byId.get(id);
    const level = step?.needs.length ? Math.max(...step.needs.map((need) => levelOf(need, visiting))) + 1 : 0;
    visiting.delete(id);
    levels.set(id, level);
    return level;
  };
  steps.forEach((step) => levelOf(step.id));
  const grouped = new Map<number, TeamRunStep[]>();
  steps.forEach((step) => {
    const level = levels.get(step.id) ?? 0;
    grouped.set(level, [...(grouped.get(level) ?? []), step]);
  });
  const maxLevel = Math.max(0, ...levels.values());
  const maxRows = Math.max(1, ...[...grouped.values()].map((rows) => rows.length));
  const finishX = RUN_WORKER_START_X + maxLevel * RUN_NODE_GAP_X + RUN_NODE_WIDTH + 120;
  const width = Math.max(760, finishX + RUN_NODE_WIDTH + 40);
  const height = Math.max(300, 20 + maxRows * RUN_NODE_GAP_Y);
  const nodes: RunGraphNode[] = [];
  grouped.forEach((rows, level) => {
    const blockHeight = (rows.length - 1) * RUN_NODE_GAP_Y;
    rows.forEach((step, row) => nodes.push({
      step,
      x: RUN_WORKER_START_X + level * RUN_NODE_GAP_X,
      y: height / 2 - RUN_NODE_HEIGHT / 2 - blockHeight / 2 + row * RUN_NODE_GAP_Y,
    }));
  });
  return { nodes, width, height, finishX };
}

function runEdge(fromX: number, fromY: number, toX: number, toY: number) {
  const bend = Math.max(45, (toX - fromX) * .45);
  return `M ${fromX} ${fromY} C ${fromX + bend} ${fromY}, ${toX - bend} ${toY}, ${toX} ${toY}`;
}

function elapsed(startedAt: string | null, endedAt: string | null, now: number) {
  if (!startedAt) return 'Not started';
  const start = Date.parse(startedAt);
  const end = endedAt ? Date.parse(endedAt) : now;
  if (!Number.isFinite(start) || !Number.isFinite(end)) return 'In progress';
  const seconds = Math.max(0, Math.round((end - start) / 1000));
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

function LiveRunGraph({
  run,
  agents,
  selectedStepId,
  onSelectStep,
  insights,
  now,
  startAgentId,
  coordinatorAgentId,
  onOpenConversation,
  onOpenTerminalSession,
}: {
  run: RunGraphRecord;
  agents: Agent[];
  selectedStepId: string;
  onSelectStep: (id: string) => void;
  insights: Record<string, NodeConversationInsight>;
  now: number;
  startAgentId: string;
  coordinatorAgentId: string;
  onOpenConversation: (step: TeamRunStep) => void;
  onOpenTerminalSession: (
    agentId: string,
    conversationId: string | undefined,
    status: TeamRunStep['status'],
    role: 'Coordinator' | 'Synthesizer',
  ) => void;
}) {
  const layout = runGraphLayout(run.steps);
  const byId = new Map(layout.nodes.map((node) => [node.step.id, node]));
  const dependedOn = new Set(run.steps.flatMap((step) => step.needs));
  const roots = layout.nodes.filter((node) => node.step.needs.length === 0);
  const leaves = layout.nodes.filter((node) => !dependedOn.has(node.step.id));
  const allWorkersFinished = run.steps.length > 0 && run.steps.every((step) => isRunTerminal(step.status));
  const coordinatorStatus = run.status === 'completed'
    ? 'completed'
    : run.status === 'failed' || run.status === 'cancelled'
      ? run.status
      : allWorkersFinished
        ? 'running'
        : 'pending';
  const coordinatorSelected = !run.steps.some((step) => step.id === selectedStepId);
  const startAgent = agents.find((agent) => agent.id === startAgentId);
  const startSessionId = run.coordinator_conversation_id ?? undefined;
  const startMessages = startAgent?.conversations.reduce((total, session) => total + session.messages, 0) ?? 0;
  const startStatus: TeamRunStep['status'] = run.status === 'pending' ? 'pending' : 'completed';
  const coordinatorAgent = agents.find((agent) => agent.id === coordinatorAgentId);
  const coordinatorSessionId = run.synthesis_conversation_id ?? undefined;
  const coordinatorMessages = coordinatorAgent?.conversations.reduce((total, session) => total + session.messages, 0) ?? 0;

  return (
    <GraphViewport className="run-live-layout" width={layout.width} height={layout.height}>
      <div className="run-dag" style={{ width: layout.width, height: layout.height }} aria-label="Live team workflow">
        <div className="run-dag-grid" aria-hidden="true" />
        <svg viewBox={`0 0 ${layout.width} ${layout.height}`} aria-hidden="true">
          <defs>
            <marker id="run-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" />
            </marker>
          </defs>
          {roots.map(({ step, x, y }) => (
            <path key={`start-${step.id}`} className={`run-dag-edge ${step.status}`} d={runEdge(RUN_START_X + RUN_NODE_WIDTH, layout.height / 2, x, y + RUN_NODE_HEIGHT / 2)} />
          ))}
          {layout.nodes.flatMap(({ step, x, y }) => step.needs.map((need) => {
            const parent = byId.get(need);
            if (!parent) return null;
            return (
              <path
                key={`${need}-${step.id}`}
                className={`run-dag-edge ${step.status}`}
                d={runEdge(parent.x + RUN_NODE_WIDTH, parent.y + RUN_NODE_HEIGHT / 2, x, y + RUN_NODE_HEIGHT / 2)}
              />
            );
          }))}
          {leaves.map(({ step, x, y }) => (
            <path key={`${step.id}-finish`} className={`run-dag-edge ${coordinatorStatus}`} d={runEdge(x + RUN_NODE_WIDTH, y + RUN_NODE_HEIGHT / 2, layout.finishX, layout.height / 2)} />
          ))}
        </svg>
        <div
          className={`run-dag-node terminal-agent start-agent ${startStatus}`}
          style={{ left: RUN_START_X, top: layout.height / 2 - RUN_NODE_HEIGHT / 2 }}
        >
          <button
            className="run-node-select"
            onClick={() => onOpenTerminalSession(startAgentId, startSessionId, startStatus, 'Coordinator')}
            aria-label={`Start coordinator: ${startStatus}`}
          >
            <span className="run-node-state"><Play size={14} fill="currentColor" /></span>
            <span className="run-node-copy">
              <strong>{startAgent?.title ?? 'Coordinator'}</strong>
              <small>{startAgent?.model || 'Start agent'}</small>
            </span>
            <span className="run-node-status">{startStatus}</span>
            <span className="run-node-metrics" aria-label="Start agent metrics">
              <span title="Model"><Bot size={10} />{startAgent?.model || '—'}</span>
              <span title="Session history"><MessageSquare size={10} />{startAgent?.conversations.length ?? 0}</span>
              <span title="Historical messages"><List size={10} />{compactNumber(startMessages)}</span>
              <span title="Agent status"><Clock size={10} />{startAgent?.status || 'ready'}</span>
            </span>
          </button>
          <button
            className="run-node-more"
            onClick={() => onOpenTerminalSession(startAgentId, startSessionId, startStatus, 'Coordinator')}
            aria-label={`View ${startAgent?.title ?? 'coordinator'} session`}
            title={startSessionId ? 'View run session' : 'No session yet'}
          >
            <MoreHorizontal size={15} />
          </button>
        </div>
        {layout.nodes.map(({ step, x, y }) => {
          const agent = agents.find((candidate) => candidate.id === step.agent_id);
          const insight = step.conversation_id ? insights[step.conversation_id] : undefined;
          return (
            <div
              key={step.id}
              className={`run-dag-node ${step.status} ${selectedStepId === step.id ? 'selected' : ''}`}
              style={{ left: x, top: y }}
            >
              <button
                className="run-node-select"
                onClick={() => onSelectStep(step.id)}
                aria-label={`${agent?.title ?? step.role}: ${step.status}`}
              >
                <span className="run-node-state">
                  {step.status === 'running' ? <Loader2 size={15} /> : step.status === 'completed' ? <Check size={14} /> : step.status === 'failed' ? <X size={14} /> : <i />}
                </span>
                <span className="run-node-copy">
                  <strong>{agent?.title ?? step.role}</strong>
                  <small>{step.role}</small>
                </span>
                <span className="run-node-status">{step.status}</span>
                <span className="run-node-metrics" aria-label="Node execution metrics">
                  <span title="Tokens used"><Coins size={10} />{compactNumber(insight?.usage?.totalTokens)}</span>
                  <span title="Reasoning steps"><Brain size={10} />{compactNumber(insight?.reasoningSteps ?? insight?.usage?.steps)}</span>
                  <span title="Execution time"><Clock size={10} />{elapsed(step.started_at, step.ended_at, now)}</span>
                  <span title="Total messages"><MessageSquare size={10} />{compactNumber(insight?.usage?.messages ?? insight?.messages.length)}</span>
                </span>
              </button>
              <button
                className="run-node-more"
                onClick={() => onOpenConversation(step)}
                aria-label={`View ${agent?.title ?? step.role} session`}
                title="View session details"
              >
                <MoreHorizontal size={15} />
              </button>
            </div>
          );
        })}
        <div
          className={`run-dag-node terminal-agent finish-agent ${coordinatorStatus} ${coordinatorSelected ? 'selected' : ''}`}
          style={{ left: layout.finishX, top: layout.height / 2 - RUN_NODE_HEIGHT / 2 }}
        >
          <button className="run-node-select" onClick={() => onSelectStep('')} aria-label={`Coordinator: ${coordinatorStatus}`}>
            <span className="run-node-state">
              {coordinatorStatus === 'running' ? <Loader2 size={15} /> : coordinatorStatus === 'completed' ? <Check size={14} /> : <Bot size={14} />}
            </span>
            <span className="run-node-copy">
              <strong>{coordinatorAgent?.title ?? 'Coordinator'}</strong>
              <small>{coordinatorAgent?.model || 'Synthesis agent'}</small>
            </span>
            <span className="run-node-status">{coordinatorStatus === 'running' ? 'synthesizing' : coordinatorStatus}</span>
            <span className="run-node-metrics" aria-label="Coordinator agent metrics">
              <span title="Model"><Bot size={10} />{coordinatorAgent?.model || '—'}</span>
              <span title="Session history"><MessageSquare size={10} />{coordinatorAgent?.conversations.length ?? 0}</span>
              <span title="Historical messages"><List size={10} />{compactNumber(coordinatorMessages)}</span>
              <span title="Agent status"><Clock size={10} />{coordinatorAgent?.status || 'ready'}</span>
            </span>
          </button>
          <button
            className="run-node-more"
            onClick={() => onOpenTerminalSession(coordinatorAgentId, coordinatorSessionId, coordinatorStatus, 'Synthesizer')}
            aria-label={`View ${coordinatorAgent?.title ?? 'synthesizer'} session`}
            title={coordinatorSessionId ? 'View run session' : 'No session yet'}
          >
            <MoreHorizontal size={15} />
          </button>
        </div>
      </div>
    </GraphViewport>
  );
}

function savedWorkflowRun(team: Team): RunGraphRecord {
  const workflow = team.workflow?.length
    ? team.workflow
    : team.members.map((member, index) => ({
      id: `worker-${index + 1}`,
      task: '',
      agent_id: member.agent_id,
      role: member.role,
      needs: [],
      allowed_tools: member.allowed_tools,
      skills: [],
    }));
  return {
    status: 'pending',
    steps: workflow.map((step) => ({
      id: step.id,
      agent_id: step.agent_id ?? '',
      role: step.role ?? step.id,
      task: step.task,
      needs: step.needs ?? [],
      allowed_tools: step.allowed_tools ?? [],
      skills: step.skills ?? [],
      status: 'pending',
      summary: '',
      summary_chars: 0,
      error: null,
      conversation_id: null,
      started_at: null,
      ended_at: null,
    })),
  };
}

function TeamRunsPanel({
  team,
  agents,
  state,
  task,
  setTask,
  onRun,
  requestedRunId,
  onSelectRun,
  routeAgentId,
  routeConversationId,
  onConversationNavigate,
  onOpenChat,
}: {
  team: Team;
  agents: Agent[];
  state: TeamsState;
  task: string;
  setTask: (value: string) => void;
  onRun: () => Promise<void>;
  requestedRunId: string;
  onSelectRun: (runId: string, replace?: boolean) => void;
  routeAgentId?: string;
  routeConversationId?: string;
  onConversationNavigate?: (agentId: string, conversationId: string) => void;
  onOpenChat?: (agentId: string, conversationId: string) => void;
}) {
  const { runs, activeRun, runsStatus, openRun, deleteRun, cancelRun } = state;
  const teamRuns = runs.filter((historyRun) => historyRun.team_id === team.id);
  const run = activeRun?.team_id === team.id ? activeRun : undefined;
  const canCancel = Boolean(run && !isRunTerminal(run.status));
  const [selectedStepId, setSelectedStepId] = useState('');
  const [graphOpen, setGraphOpen] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [now, setNow] = useState(Date.now());
  const [conversationStep, setConversationStep] = useState<TeamRunStep>();
  const [runToDelete, setRunToDelete] = useState<TeamRunRecord>();
  const [deletingRun, setDeletingRun] = useState(false);
  const [insights, setInsights] = useState<Record<string, NodeConversationInsight>>({});
  const selectedStep = run?.steps.find((step) => step.id === selectedStepId);
  const graphRun = run ?? savedWorkflowRun(team);
  const allWorkersFinished = Boolean(run?.steps.length && run.steps.every((step) => isRunTerminal(step.status)));
  const coordinatorWorking = run?.status === 'running' && allWorkersFinished;

  useEffect(() => {
    if (!run) return;
    const running = run.steps.filter((step) => step.status === 'running');
    if (running.length) {
      setSelectedStepId((current) => running.some((step) => step.id === current) ? current : running[0].id);
    } else if (run.status === 'running' && run.steps.every((step) => isRunTerminal(step.status))) {
      setSelectedStepId('');
    } else {
      setSelectedStepId((current) => run.steps.some((step) => step.id === current) ? current : run.steps[0]?.id ?? '');
    }
  }, [run?.id, run?.revision, run]);

  useEffect(() => {
    if (!requestedRunId || run?.id === requestedRunId) return;
    void openRun(team.id, requestedRunId);
  }, [openRun, requestedRunId, run?.id, team.id]);

  useEffect(() => {
    if (routeConversationId === undefined) return;
    if (!routeConversationId || !routeAgentId) {
      setConversationStep(undefined);
      return;
    }
    const routedStep = run?.steps.find((step) =>
      step.agent_id === routeAgentId && step.conversation_id === routeConversationId);
    if (routedStep) {
      setConversationStep(routedStep);
      return;
    }
    const startAgentId = run?.orchestrator_id || team.orchestrator_id;
    const synthesisAgentId = team.synthesis_agent_id || startAgentId;
    const terminalRole = routeAgentId === synthesisAgentId ? 'Synthesizer'
      : routeAgentId === startAgentId ? 'Coordinator'
        : undefined;
    const terminalSessionExists = terminalRole
      && agents.find((agent) => agent.id === routeAgentId)?.conversations.some((session) => session.id === routeConversationId);
    if (!terminalSessionExists || !terminalRole) {
      setConversationStep(undefined);
      return;
    }
    const workersFinished = Boolean(run?.steps.length && run.steps.every((step) => isRunTerminal(step.status)));
    const status: TeamRunStep['status'] = run?.status === 'completed'
      ? 'completed'
      : run?.status === 'failed' || run?.status === 'cancelled'
        ? run.status
        : workersFinished ? 'running' : 'pending';
    setConversationStep({
      id: terminalRole === 'Coordinator' ? START_STAGE_ID : FINISH_STAGE_ID,
      agent_id: routeAgentId,
      role: terminalRole,
      task: terminalRole === 'Coordinator'
        ? team.coordinator_prompt ?? DEFAULT_START_PROMPT
        : run?.synthesis_instruction ?? team.synthesis_instruction ?? DEFAULT_FINISH_PROMPT,
      needs: terminalRole === 'Coordinator' ? [] : run?.steps.map((step) => step.id) ?? [],
      allowed_tools: terminalRole === 'Coordinator'
        ? team.coordinator_allowed_tools ?? []
        : team.synthesis_allowed_tools ?? [],
      skills: terminalRole === 'Coordinator' ? team.coordinator_skills ?? [] : team.synthesis_skills ?? [],
      status,
      summary: terminalRole === 'Synthesizer' ? run?.orchestrator_summary ?? '' : '',
      summary_chars: terminalRole === 'Synthesizer' ? run?.orchestrator_summary.length ?? 0 : 0,
      error: run?.error ?? null,
      conversation_id: routeConversationId,
      started_at: run?.started_at ?? null,
      ended_at: run?.ended_at ?? null,
    });
  }, [agents, routeAgentId, routeConversationId, run, team]);

  useEffect(() => {
    if (!canCancel) return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [canCancel]);

  useEffect(() => {
    const targets = run?.steps.filter((step) => step.conversation_id) ?? [];
    if (!targets.length) return undefined;
    let active = true;
    targets.forEach((step) => {
      const conversationId = step.conversation_id as string;
      setInsights((current) => ({
        ...current,
        [conversationId]: { ...(current[conversationId] ?? conversationInsight([])), status: 'loading' },
      }));
      void loadConversationInsight(step.agent_id, conversationId, run?.revision ?? 0).then((insight) => {
        if (!active) return;
        setInsights((current) => ({ ...current, [conversationId]: insight }));
      });
    });
    return () => { active = false; };
  }, [run?.id, run?.revision]);

  const cancel = async () => {
    if (!run) return;
    setCancelling(true);
    try {
      await cancelRun(team.id, run.id);
    } finally {
      setCancelling(false);
    }
  };

  const confirmDeleteRun = async () => {
    if (!runToDelete || deletingRun) return;
    setDeletingRun(true);
    try {
      await deleteRun(team.id, runToDelete.id);
      if (requestedRunId === runToDelete.id) onSelectRun('');
      setRunToDelete(undefined);
    } finally {
      setDeletingRun(false);
    }
  };

  const send = async () => {
    if (canCancel || submitting || state.pending || !task.trim()) return;
    setSubmitting(true);
    try {
      await onRun();
    } finally {
      setSubmitting(false);
    }
  };

  const showConversation = (step: TeamRunStep) => {
    setConversationStep(step);
    if (step.conversation_id) onConversationNavigate?.(step.agent_id, step.conversation_id);
  };

  const showTerminalConversation = (
    agentId: string,
    conversationId: string | undefined,
    status: TeamRunStep['status'],
    role: 'Coordinator' | 'Synthesizer',
  ) => {
    setConversationStep({
      id: role === 'Coordinator' ? START_STAGE_ID : FINISH_STAGE_ID,
      agent_id: agentId,
      role,
      task: role === 'Coordinator'
        ? team.coordinator_prompt ?? DEFAULT_START_PROMPT
        : run?.synthesis_instruction ?? team.synthesis_instruction ?? DEFAULT_FINISH_PROMPT,
      needs: role === 'Coordinator' ? [] : run?.steps.map((step) => step.id) ?? [],
      allowed_tools: role === 'Coordinator'
        ? team.coordinator_allowed_tools ?? []
        : team.synthesis_allowed_tools ?? [],
      skills: role === 'Coordinator' ? team.coordinator_skills ?? [] : team.synthesis_skills ?? [],
      status,
      summary: role === 'Synthesizer' ? run?.orchestrator_summary ?? '' : '',
      summary_chars: role === 'Synthesizer' ? run?.orchestrator_summary.length ?? 0 : 0,
      error: run?.error ?? null,
      conversation_id: conversationId ?? null,
      started_at: run?.started_at ?? null,
      ended_at: run?.ended_at ?? null,
    });
    if (conversationId) onConversationNavigate?.(agentId, conversationId);
  };

  const closeConversation = () => {
    setConversationStep(undefined);
    onConversationNavigate?.('', '');
  };

  const conversationAgent = conversationStep
    ? agents.find((agent) => agent.id === conversationStep.agent_id)
    : undefined;

  return (
    <div className="team-execution-stack">
      <section className="teams-card team-execution-card">
        <button
          className="team-execution-toggle"
          aria-expanded={graphOpen}
          onClick={() => setGraphOpen((current) => !current)}
        >
          <span className="team-execution-toggle-icon"><GitBranch size={16} /></span>
          <span>
            <strong>Execution details</strong>
            <small>{run ? `Run ${run.id.replace(/^tr_/, '').slice(0, 8)} · ${run.status}` : `${team.workflow?.length || team.members.length} workflow stages`}</small>
          </span>
          {run && <span className={`run-chip ${run.status}`}>{run.status}</span>}
          <ChevronDown size={16} className={graphOpen ? 'open' : ''} />
        </button>

        {graphOpen && (
          <div className="team-execution-body">
            {run ? (
              <>
                <div className="run-progress-summary">
                  <span><strong>{run.steps.filter((step) => step.status === 'completed').length}</strong> of {run.steps.length} agents complete</span>
                  <span>
                    {run.steps.filter((step) => step.status === 'running').length + (coordinatorWorking ? 1 : 0)} active now
                  </span>
                  <span>Revision {run.revision}</span>
                </div>
              </>
            ) : (
              <div className="run-progress-summary">
                <span><strong>0</strong> of {graphRun.steps.length} agents complete</span>
                <span>0 active now</span>
                <span>Ready</span>
              </div>
            )}
            <LiveRunGraph
              run={graphRun}
              agents={agents}
              selectedStepId={selectedStepId}
              onSelectStep={run ? setSelectedStepId : () => undefined}
              insights={insights}
              now={now}
              startAgentId={run?.orchestrator_id || team.orchestrator_id}
              coordinatorAgentId={team.synthesis_agent_id || run?.orchestrator_id || team.orchestrator_id}
              onOpenConversation={showConversation}
              onOpenTerminalSession={showTerminalConversation}
            />
            {run?.error && <p className="teams-error">Run failed: {run.error}</p>}
          </div>
        )}

        <div className="run-history-compact">
          <span className="run-history-label">Executions</span>
          {runsStatus === 'loading' && <small>Loading…</small>}
          {runsStatus !== 'loading' && teamRuns.length === 0 && <small>No runs yet</small>}
          {teamRuns.slice(0, 5).map((historyRun: TeamRunRecord) => (
            <div
              key={historyRun.id}
              className={`run-history-item ${run?.id === historyRun.id ? 'active' : ''}`}
            >
              <button
                className="run-history-open"
                onClick={() => {
                  onSelectRun(historyRun.id);
                  void openRun(team.id, historyRun.id);
                }}
                aria-label={`Open run ${historyRun.id.replace(/^tr_/, '').slice(0, 8)}`}
              >
                <span className={`run-activity-dot ${historyRun.status}`} />
                <strong>{historyRun.id.replace(/^tr_/, '').slice(0, 8)}</strong>
                <small>{historyRun.status}</small>
              </button>
              <button
                className="run-history-delete"
                disabled={!isRunTerminal(historyRun.status)}
                onClick={() => setRunToDelete(historyRun)}
                aria-label={`Delete run ${historyRun.id.replace(/^tr_/, '').slice(0, 8)}`}
                title={isRunTerminal(historyRun.status) ? 'Delete execution' : 'Cancel this execution before deleting it'}
              >
                <Trash2 size={12} />
              </button>
            </div>
          ))}
          {teamRuns.length > 5 && <span className="run-history-more">+{teamRuns.length - 5}</span>}
        </div>
      </section>

      <section className="teams-card team-results-card" aria-live="polite">
        <div className="team-results-heading">
          <span><Bot size={15} /></span>
          <div><strong>Results</strong><small>{run ? 'Select a graph node to inspect its latest output' : 'The Team response will appear here'}</small></div>
          {selectedStep && <span className={`run-chip ${selectedStep.status}`}>{selectedStep.status}</span>}
        </div>

        {selectedStep ? (
          <div className="team-result-content">
            <div className="team-result-source">
              <span className={`run-activity-dot ${selectedStep.status}`} />
              <strong>{agents.find((agent) => agent.id === selectedStep.agent_id)?.title ?? selectedStep.role}</strong>
              <small>{selectedStep.status === 'running' ? 'Working now' : selectedStep.status}</small>
              <span><Clock size={12} /> {elapsed(selectedStep.started_at, selectedStep.ended_at, now)}</span>
            </div>
            {selectedStep.status === 'running' && (
              <div className="run-working compact">
                <span><i /><i /><i /></span>
                <strong>Team is working</strong>
                <p>The result will update here when this stage finishes.</p>
              </div>
            )}
            {selectedStep.status === 'pending' && (
              <div className="run-waiting compact"><Clock size={15} /><p>Waiting for {selectedStep.needs.length ? selectedStep.needs.join(', ') : 'an execution slot'}.</p></div>
            )}
            {(selectedStep.summary || selectedStep.error) && (
              <div className={`run-agent-output ${selectedStep.error ? 'error' : ''}`}>
                <span>{selectedStep.error ? 'Error' : `Returned output · ${selectedStep.summary_chars.toLocaleString()} chars`}</span>
                <p>{selectedStep.summary || selectedStep.error}</p>
              </div>
            )}
            {isRunTerminal(selectedStep.status) && !selectedStep.summary && !selectedStep.error && (
              <p className="team-results-empty">This stage returned no text output.</p>
            )}
          </div>
        ) : coordinatorWorking ? (
          <div className="run-working compact">
            <span><i /><i /><i /></span>
            <strong>Building the final answer</strong>
            <p>All agent stages are complete. The coordinator is combining their results.</p>
          </div>
        ) : run?.orchestrator_summary ? (
          <div className="team-result-content final">
            <span className="team-final-label"><Check size={13} /> Final answer</span>
            <p>{run.orchestrator_summary}</p>
          </div>
        ) : run ? (
          <div className="team-results-empty">
            <MousePointer2 size={17} />
            <span>Select a node in the execution graph to inspect its result.</span>
          </div>
        ) : (
          <div className="team-results-empty">
            <Bot size={17} />
            <span>Send an objective below to start this Team.</span>
          </div>
        )}
      </section>

      <form
        className={`team-chat-composer ${canCancel ? 'is-running' : ''}`}
        onSubmit={(event) => {
          event.preventDefault();
          void send();
        }}
      >
        <textarea
          value={task}
          disabled={canCancel || submitting || state.pending}
          aria-label="Team objective"
          onChange={(event) => setTask(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && !canCancel && !submitting && !state.pending) {
              event.preventDefault();
              void send();
            }
          }}
          placeholder={canCancel ? 'The Team is working on your objective…' : 'Message this Team…'}
          rows={1}
        />
        <div className="team-composer-hint">
          {canCancel ? (
            <span><Loader2 size={12} /> Execution updates are streaming to the graph</span>
          ) : (
            <span>Enter to send · Shift+Enter for a new line</span>
          )}
        </div>
        {canCancel ? (
          <button
            type="button"
            className="team-composer-action cancel"
            disabled={cancelling}
            onClick={() => void cancel()}
            aria-label="Cancel run"
          >
            {cancelling ? <Loader2 size={15} /> : <Square size={13} fill="currentColor" />}
            <span>{cancelling ? 'Cancelling…' : 'Cancel'}</span>
          </button>
        ) : (
          <button type="submit" className="team-composer-action send" disabled={submitting || state.pending || !task.trim()} aria-label="Send objective">
            {submitting || state.pending ? <Loader2 size={16} /> : <Send size={16} />}
            <span>{submitting || state.pending ? 'Starting…' : 'Send'}</span>
          </button>
        )}
      </form>
      {conversationStep && (
        <SessionConversationModal
          agent={conversationAgent}
          conversationId={conversationStep.conversation_id ?? ''}
          title={conversationAgent?.title ?? conversationStep.role}
          subtitle={`${conversationStep.role} · ${conversationStep.status}`}
          statusTone={conversationStep.status === 'running' ? 'running'
            : conversationStep.status === 'completed' ? 'completed'
            : conversationStep.status === 'failed' ? 'failed'
            : 'idle'}
          executionTime={elapsed(conversationStep.started_at, conversationStep.ended_at, now)}
          fallback={conversationStep.conversation_id ? insights[conversationStep.conversation_id] : undefined}
          emptyTitle="No session yet"
          emptyDescription="This trace becomes available after the agent starts this node."
          closeLabel="Close node session"
          onOpenInConversation={onOpenChat && conversationStep.conversation_id
            ? () => onOpenChat(conversationStep.agent_id, conversationStep.conversation_id as string)
            : undefined}
          onClose={closeConversation}
        />
      )}
      {runToDelete && (
        <ConfirmDialog
          title="Delete this execution?"
          message={`Run ${runToDelete.id.replace(/^tr_/, '').slice(0, 8)} will be permanently removed from this Team's execution history. Agent sessions will not be deleted.`}
          confirmLabel={deletingRun ? 'Deleting…' : 'Delete execution'}
          danger
          onConfirm={() => void confirmDeleteRun()}
          onCancel={() => {
            if (!deletingRun) setRunToDelete(undefined);
          }}
        />
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

function draftNodeLevels(nodes: DraftNode[]) {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const levels = new Map<string, number>();
  const levelOf = (id: string, visiting = new Set<string>()): number => {
    if (levels.has(id)) return levels.get(id) as number;
    if (visiting.has(id)) return 0;
    visiting.add(id);
    const node = byId.get(id);
    const dependencies = (node?.needs ?? []).filter((need) => byId.has(need));
    const level = dependencies.length
      ? Math.max(...dependencies.map((need) => levelOf(need, visiting))) + 1
      : 0;
    visiting.delete(id);
    levels.set(id, level);
    return level;
  };
  nodes.forEach((node) => levelOf(node.id));
  return levels;
}

function draftCanvasLayout(nodes: DraftNode[]) {
  const levels = draftNodeLevels(nodes);
  const maxLevel = Math.max(0, ...levels.values());
  const grouped = new Map<number, DraftNode[]>();
  nodes.forEach((node) => {
    const level = levels.get(node.id) ?? 0;
    grouped.set(level, [...(grouped.get(level) ?? []), node]);
  });
  const maxRows = Math.max(1, ...[...grouped.values()].map((group) => group.length));
  const height = Math.max(CANVAS_HEIGHT, 80 + maxRows * 165);
  const finishX = Math.max(794, 170 + (maxLevel + 1) * 260 + 104);
  const width = Math.max(CANVAS_WIDTH, finishX + 106);
  return { levels, grouped, width, height, finishX };
}

function arrangeDraftNodes(nodes: DraftNode[]) {
  const layout = draftCanvasLayout(nodes);
  const positions = new Map<string, { x: number; y: number }>();
  layout.grouped.forEach((group, level) => {
    const blockHeight = (group.length - 1) * 165;
    const firstY = (layout.height - NODE_HEIGHT - blockHeight) / 2;
    group.forEach((node, row) => positions.set(node.id, {
      x: 170 + level * 260,
      y: firstY + row * 165,
    }));
  });
  return nodes.map((node) => ({ ...node, ...positions.get(node.id) }));
}

function WorkflowEdges({ nodes, width, height, finishX }: { nodes: DraftNode[]; width: number; height: number; finishX: number }) {
  const ids = new Set(nodes.map((node) => node.id));
  const roots = nodes.filter((node) => !node.needs?.some((need) => ids.has(need)));
  const used = new Set(nodes.flatMap((node) => node.needs ?? []));
  const leaves = nodes.filter((node) => !used.has(node.id));
  const terminalY = height / 2;
  return (
    <svg className="team-canvas-edges" viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
      <defs>
        <marker id="team-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" />
        </marker>
      </defs>
      {roots.map((node) => (
        <path key={`start-${node.id}`} className="team-edge virtual" d={curve(116, terminalY, node.x, node.y + NODE_HEIGHT / 2)} />
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
        <path key={`${node.id}-finish`} className="team-edge virtual" d={curve(node.x + NODE_WIDTH, node.y + NODE_HEIGHT / 2, finishX + 2, terminalY)} />
      ))}
    </svg>
  );
}

function TeamBuilder({
  agents,
  state,
  initialTeam,
  onSaved,
  onBack,
}: {
  agents: Agent[];
  state: TeamsState;
  initialTeam?: Team;
  onSaved: (team: Team) => void;
  onBack: () => void;
}) {
  const enabledSkillIds = (agentId: string) => (
    agents.find((agent) => agent.id === agentId)?.skills
      .filter((skill) => skill.enabled)
      .map((skill) => skill.skill_id) ?? []
  );
  const initialWorkflow = initialTeam?.workflow?.length
    ? initialTeam.workflow
    : initialTeam?.members.map((member, index) => ({
      id: `worker-${index + 1}`,
      task: `Complete the ${member.role} stage for the team objective.`,
      agent_id: member.agent_id,
      role: member.role,
      needs: [],
      allowed_tools: member.allowed_tools.length ? member.allowed_tools : [...ESSENTIAL_TEAM_TOOLSETS],
      skills: enabledSkillIds(member.agent_id),
    })) ?? [];
  const [name, setName] = useState(initialTeam?.name ?? '');
  const [description, setDescription] = useState(initialTeam?.description ?? '');
  const [orchestratorId, setOrchestratorId] = useState(initialTeam?.orchestrator_id ?? agents[0]?.id ?? '');
  const [nodes, setNodes] = useState<DraftNode[]>(() => arrangeDraftNodes(initialWorkflow.map((step) => ({
    ...step,
    agent_id: step.agent_id ?? '',
    role: step.role ?? step.id,
    needs: step.needs ?? [],
    allowed_tools: step.allowed_tools ?? [...ESSENTIAL_TEAM_TOOLSETS],
    skills: step.skills ?? enabledSkillIds(step.agent_id ?? ''),
    x: 0,
    y: 0,
  }))));
  const [selectedNodeId, setSelectedNodeId] = useState(START_STAGE_ID);
  const [connectFrom, setConnectFrom] = useState('');
  const [message, setMessage] = useState('');
  const [communicationLevel, setCommunicationLevel] = useState<0 | 1 | 2 | 3>(initialTeam?.communication_level ?? 1);
  const [sharedWorkspace, setSharedWorkspace] = useState(initialTeam?.shared_workspace ?? false);
  const [maxParallel, setMaxParallel] = useState(initialTeam?.max_parallel ?? 3);
  const [maxDepth, setMaxDepth] = useState(initialTeam?.max_depth ?? 1);
  const [synthesisInstruction, setSynthesisInstruction] = useState(
    initialTeam?.synthesis_instruction || DEFAULT_FINISH_PROMPT,
  );
  const [coordinatorPrompt, setCoordinatorPrompt] = useState(
    initialTeam?.coordinator_prompt || DEFAULT_START_PROMPT,
  );
  const [coordinatorTools, setCoordinatorTools] = useState(
    initialTeam?.coordinator_allowed_tools ?? [...ESSENTIAL_TEAM_TOOLSETS],
  );
  const [coordinatorSkills, setCoordinatorSkills] = useState(
    initialTeam?.coordinator_skills ?? enabledSkillIds(initialTeam?.orchestrator_id ?? agents[0]?.id ?? ''),
  );
  const [synthesisAgentId, setSynthesisAgentId] = useState(
    initialTeam?.synthesis_agent_id ?? initialTeam?.orchestrator_id ?? agents[0]?.id ?? '',
  );
  const [synthesisSkills, setSynthesisSkills] = useState(
    initialTeam?.synthesis_skills
    ?? enabledSkillIds(initialTeam?.synthesis_agent_id ?? initialTeam?.orchestrator_id ?? agents[0]?.id ?? ''),
  );
  const [synthesisTools, setSynthesisTools] = useState(
    initialTeam?.synthesis_allowed_tools ?? [...ESSENTIAL_TEAM_TOOLSETS],
  );
  const [drag, setDrag] = useState<{ id: string; pointerId: number; dx: number; dy: number }>();
  const canvasRef = useRef<HTMLDivElement>(null);
  const canvasLayout = draftCanvasLayout(nodes);
  const { width: canvasWidth, height: canvasHeight, finishX } = canvasLayout;
  const selectedNode = nodes.find((node) => node.id === selectedNodeId);
  const startSelected = selectedNodeId === START_STAGE_ID;
  const finishSelected = selectedNodeId === FINISH_STAGE_ID;
  const selectedStageExists = Boolean(selectedNode || startSelected || finishSelected);
  const selectedStageAgentId = startSelected
    ? orchestratorId
    : finishSelected
      ? synthesisAgentId
      : selectedNode?.agent_id ?? '';
  const selectedAgent = agents.find((agent) => agent.id === selectedStageAgentId);
  const selectedStageTools = startSelected
    ? coordinatorTools
    : finishSelected
      ? synthesisTools
      : selectedNode?.allowed_tools ?? [];
  const selectedStageSkills = startSelected
    ? coordinatorSkills
    : finishSelected
      ? synthesisSkills
      : selectedNode?.skills ?? [];
  const availableAgents = agents;

  useEffect(() => {
    if (!agents.some((agent) => agent.id === orchestratorId)) setOrchestratorId(agents[0]?.id ?? '');
  }, [agents, orchestratorId]);

  const addAgent = (agent: Agent) => {
    const role = safeId(agent.title, `worker-${nodes.length + 1}`);
    const baseId = safeId(role, `step-${nodes.length + 1}`);
    let id = baseId;
    let suffix = 2;
    while (nodes.some((node) => node.id === id)) id = `${baseId}-${suffix++}`;
    const next: DraftNode = {
      id,
      agent_id: agent.id,
      role,
      task: `Complete the ${role.replace(/-/g, ' ')} stage for the team objective.`,
      needs: [],
      allowed_tools: [...ESSENTIAL_TEAM_TOOLSETS],
      skills: enabledSkillIds(agent.id),
      x: 0,
      y: 0,
    };
    setNodes((current) => arrangeDraftNodes([...current, next]));
    setSelectedNodeId(id);
    setMessage('');
  };

  const updateSelectedAgent = (agentId: string) => {
    const skills = enabledSkillIds(agentId);
    if (startSelected) {
      const previousId = orchestratorId;
      setOrchestratorId(agentId);
      setCoordinatorSkills(skills);
      setCoordinatorTools([...ESSENTIAL_TEAM_TOOLSETS]);
      if (synthesisAgentId === previousId) {
        setSynthesisAgentId(agentId);
        setSynthesisSkills(skills);
        setSynthesisTools([...ESSENTIAL_TEAM_TOOLSETS]);
      }
      return;
    }
    if (finishSelected) {
      setSynthesisAgentId(agentId);
      setSynthesisSkills(skills);
      setSynthesisTools([...ESSENTIAL_TEAM_TOOLSETS]);
      return;
    }
    if (selectedNode) {
      updateNode(selectedNode.id, {
        agent_id: agentId,
        allowed_tools: [...ESSENTIAL_TEAM_TOOLSETS],
        skills,
      });
    }
  };

  const updateSelectedTools = (tools: string[]) => {
    if (startSelected) setCoordinatorTools(tools);
    else if (finishSelected) setSynthesisTools(tools);
    else if (selectedNode) updateNode(selectedNode.id, { allowed_tools: tools });
  };

  const updateSelectedSkills = (skills: string[]) => {
    if (startSelected) setCoordinatorSkills(skills);
    else if (finishSelected) setSynthesisSkills(skills);
    else if (selectedNode) updateNode(selectedNode.id, { skills });
  };

  const updateNode = (id: string, patch: Partial<DraftNode>) => {
    setNodes((current) => current.map((node) => node.id === id ? { ...node, ...patch } : node));
  };

  const removeNode = (id: string) => {
    setNodes((current) => arrangeDraftNodes(current
      .filter((node) => node.id !== id)
      .map((node) => ({ ...node, needs: (node.needs ?? []).filter((need) => need !== id) }))));
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
    setNodes((current) => arrangeDraftNodes(current.map((node) => node.id === to
      ? { ...node, needs: [...new Set([...(node.needs ?? []), connectFrom])] }
      : node)));
    setConnectFrom('');
    setMessage('');
  };

  const toggleDependency = (nodeId: string, dependencyId: string) => {
    const node = nodes.find((item) => item.id === nodeId);
    if (!node) return;
    if (node.needs?.includes(dependencyId)) {
      setNodes((current) => arrangeDraftNodes(current.map((item) => item.id === nodeId
        ? { ...item, needs: (item.needs ?? []).filter((id) => id !== dependencyId) }
        : item)));
      setMessage('');
      return;
    }
    if (createsCycle(nodes, dependencyId, nodeId)) {
      setMessage('That dependency would create a cycle.');
      return;
    }
    setNodes((current) => arrangeDraftNodes(current.map((item) => item.id === nodeId
      ? { ...item, needs: [...new Set([...(item.needs ?? []), dependencyId])] }
      : item)));
    setMessage('');
  };

  const beginDrag = (event: ReactPointerEvent<HTMLDivElement>, node: DraftNode) => {
    if ((event.target as HTMLElement).closest('button, input, textarea')) return;
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;
    const scaleX = canvasWidth / rect.width;
    const scaleY = canvasHeight / rect.height;
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
    const x = (event.clientX - rect.left) * (canvasWidth / rect.width) - drag.dx;
    const y = (event.clientY - rect.top) * (canvasHeight / rect.height) - drag.dy;
    updateNode(drag.id, {
      x: Math.max(130, Math.min(finishX - NODE_WIDTH - 34, x)),
      y: Math.max(28, Math.min(canvasHeight - NODE_HEIGHT - 28, y)),
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
    const memberNodes = new Map<string, DraftNode[]>();
    nodes.forEach((node) => memberNodes.set(
      node.agent_id,
      [...(memberNodes.get(node.agent_id) ?? []), node],
    ));
    const input: TeamInput = {
      name: name.trim(),
      description: description.trim() || undefined,
      orchestrator_id: orchestratorId,
      coordinator_prompt: coordinatorPrompt.trim(),
      coordinator_allowed_tools: coordinatorTools,
      coordinator_skills: coordinatorSkills,
      synthesis_agent_id: synthesisAgentId || orchestratorId,
      synthesis_allowed_tools: synthesisTools,
      synthesis_skills: synthesisSkills,
      members: [...memberNodes.entries()].map(([agentId, stages]) => ({
        agent_id: agentId,
        role: stages[0].role.trim(),
        allowed_tools: stages.some((stage) => !(stage.allowed_tools?.length))
          ? []
          : [...new Set(stages.flatMap((stage) => stage.allowed_tools ?? []))],
        enabled: true,
      })),
      workflow: nodes.map(({ x: _x, y: _y, ...node }) => ({
        ...node,
        role: node.role.trim(),
        task: node.task.trim(),
        needs: node.needs ?? [],
        allowed_tools: node.allowed_tools ?? [],
        skills: node.skills ?? [],
      })),
      shared_workspace: sharedWorkspace || communicationLevel >= 2,
      communication_level: communicationLevel,
      synthesis_instruction: synthesisInstruction.trim(),
      max_parallel: Math.max(1, Math.min(maxParallel, nodes.length)),
      max_depth: maxDepth,
      enabled: true,
    };
    try {
      onSaved(initialTeam
        ? await state.update({ id: initialTeam.id, ...input })
        : await state.create(input));
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : 'Could not save the team.');
    }
  };

  return (
    <div className={`team-builder-shell ${selectedStageExists ? 'has-inspector' : ''}`}>
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
            Description
            <textarea
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="Describe what this team is best at…"
              maxLength={2000}
            />
          </label>
        </div>
        <details className="team-policy-settings">
          <summary>Execution & communication</summary>
          <label>
            Communication level
            <select
              aria-label="Communication level"
              value={communicationLevel}
              onChange={(event) => setCommunicationLevel(Number(event.target.value) as 0 | 1 | 2 | 3)}
            >
              {COMMUNICATION_LEVELS.map((level) => (
                <option key={level.value} value={level.value}>{level.label}</option>
              ))}
            </select>
            <small>{COMMUNICATION_LEVELS[communicationLevel].description}</small>
          </label>
          <label>
            Parallel agents
            <input
              aria-label="Parallel agents"
              type="number"
              min={1}
              max={64}
              value={maxParallel}
              onChange={(event) => setMaxParallel(Math.max(1, Number(event.target.value) || 1))}
            />
          </label>
          <label>
            Delegation depth
            <input
              aria-label="Delegation depth"
              type="number"
              min={1}
              max={8}
              value={maxDepth}
              onChange={(event) => setMaxDepth(Math.max(1, Math.min(8, Number(event.target.value) || 1)))}
            />
          </label>
          <label className="team-policy-check">
            <input
              type="checkbox"
              checked={sharedWorkspace || communicationLevel >= 2}
              disabled={communicationLevel >= 2}
              onChange={(event) => setSharedWorkspace(event.target.checked)}
            />
            Shared run workspace
          </label>
        </details>
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
          <span><strong>Per-agent capabilities</strong><small>Configure tools, skills, dependencies, and context on each stage.</small></span>
        </div>
      </aside>

      <main className="team-builder-main">
        <div className="team-builder-toolbar">
          <div>
            <span className="teams-kicker">Visual workflow</span>
            <h2>{name.trim() || 'Untitled team'}</h2>
          </div>
          <div className="team-toolbar-help"><MousePointer2 size={14} /> Drag cards to arrange · drag background to pan · scroll to zoom</div>
          <button className="primary-button" disabled={state.pending} onClick={() => void save()}>
            <Save size={15} /> {state.pending ? 'Saving…' : initialTeam ? 'Save changes' : 'Save team'}
          </button>
        </div>
        {message && <div className="teams-inline-message" role="alert">{message}</div>}
        <GraphViewport
          className="team-canvas-wrap"
          width={canvasWidth}
          height={canvasHeight}
          onArrange={() => setNodes((current) => arrangeDraftNodes(current))}
        >
          <div
            ref={canvasRef}
            className={`team-canvas ${connectFrom ? 'is-connecting' : ''}`}
            style={{ width: canvasWidth, height: canvasHeight }}
            onPointerMove={moveDrag}
            onPointerUp={() => setDrag(undefined)}
            onPointerCancel={() => setDrag(undefined)}
          >
            <div className="team-canvas-grid" />
            <WorkflowEdges nodes={nodes} width={canvasWidth} height={canvasHeight} finishX={finishX} />
            <button
              type="button"
              className={`team-terminal-node start ${startSelected ? 'selected' : ''}`}
              style={{ left: 32, top: canvasHeight / 2 - 40 }}
              onClick={() => setSelectedNodeId(START_STAGE_ID)}
              aria-label="Configure Start stage"
            >
              <span><Play size={14} fill="currentColor" /></span>
              <strong>Start</strong>
              <small>{agents.find((agent) => agent.id === orchestratorId)?.title ?? 'Coordinator'}</small>
            </button>
            {nodes.map((node) => {
              const agent = agents.find((candidate) => candidate.id === node.agent_id);
              return (
                <div
                  key={node.id}
                  role="button"
                  tabIndex={0}
                  aria-label={`Configure ${node.id} stage`}
                  className={`team-workflow-node ${selectedNodeId === node.id ? 'selected' : ''}`}
                  style={{ left: node.x, top: node.y }}
                  onPointerDown={(event) => beginDrag(event, node)}
                  onClick={() => setSelectedNodeId(node.id)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') setSelectedNodeId(node.id);
                  }}
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
                  <div className="team-node-foot">
                    <span>{node.allowed_tools?.length ? `${node.allowed_tools.length} TOOLS` : 'AGENT DEFAULTS'}</span>
                    <span>{node.needs?.length ?? 0} input{node.needs?.length === 1 ? '' : 's'}</span>
                  </div>
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
            <button
              type="button"
              className={`team-terminal-node finish ${finishSelected ? 'selected' : ''}`}
              style={{ left: finishX, top: canvasHeight / 2 - 40 }}
              onClick={() => setSelectedNodeId(FINISH_STAGE_ID)}
              aria-label="Configure Finish stage"
            >
              <span><Check size={15} /></span>
              <strong>Finish</strong>
              <small>{agents.find((agent) => agent.id === synthesisAgentId)?.title ?? 'Synthesizer'}</small>
            </button>
            {nodes.length === 0 && (
              <div className="team-canvas-empty">
                <div><Plus size={22} /></div>
                <strong>Add your first agent</strong>
                <span>Choose a profile from the left. Then add more agents and connect their ports.</span>
              </div>
            )}
          </div>
        </GraphViewport>
      </main>

      <aside className={`team-node-inspector ${selectedStageExists ? 'open' : ''}`}>
        {selectedStageExists ? (
          <>
            <div className="team-inspector-head">
              <div>
                <span className="teams-kicker">Stage settings</span>
                <h3>{startSelected ? 'Start' : finishSelected ? 'Finish' : selectedNode?.id}</h3>
              </div>
              <button className="icon-button" aria-label="Close stage settings" onClick={() => setSelectedNodeId('')}><X size={16} /></button>
            </div>
            {selectedNode && (
              <label>
                Role
                <input value={selectedNode.role} onChange={(event) => updateNode(selectedNode.id, { role: event.target.value })} />
              </label>
            )}
            <label>
              Instructions
              <textarea
                aria-label="Stage instructions"
                value={startSelected ? coordinatorPrompt : finishSelected ? synthesisInstruction : selectedNode?.task ?? ''}
                onChange={(event) => {
                  if (startSelected) setCoordinatorPrompt(event.target.value);
                  else if (finishSelected) setSynthesisInstruction(event.target.value);
                  else if (selectedNode) updateNode(selectedNode.id, { task: event.target.value });
                }}
              />
            </label>
            <label>
              Agent
              <select
                aria-label="Stage agent"
                value={selectedStageAgentId}
                onChange={(event) => updateSelectedAgent(event.target.value)}
              >
                {agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.title}</option>)}
              </select>
            </label>
            {selectedNode && (
              <div className="team-inspector-section">
                <span>Depends on</span>
                {(selectedNode.needs ?? []).length === 0 && <small>No dependencies — starts after Start</small>}
                <div className="team-inspector-options">
                  {nodes.filter((node) => node.id !== selectedNode.id).map((node) => (
                    <label key={node.id}>
                      <input
                        type="checkbox"
                        checked={selectedNode.needs?.includes(node.id) ?? false}
                        onChange={() => toggleDependency(selectedNode.id, node.id)}
                      />
                      {node.id}
                    </label>
                  ))}
                </div>
              </div>
            )}
            <div className="team-inspector-section">
              <span>Tool access</span>
              <small>Essential toolsets are selected by default. Clear all to inherit the agent profile.</small>
              <div className="team-inspector-options">
                {TEAM_TOOLSETS.map(([id, label]) => (
                  <label key={id}>
                    <input
                      type="checkbox"
                      checked={selectedStageTools.includes(id)}
                      onChange={(event) => updateSelectedTools(event.target.checked
                        ? [...new Set([...selectedStageTools, id])]
                        : selectedStageTools.filter((tool) => tool !== id))}
                    />
                    {label}
                  </label>
                ))}
              </div>
            </div>
            <div className="team-inspector-section">
              <span>Preloaded skills</span>
              <small>Selected skills are loaded before this stage starts.</small>
              <div className="team-inspector-options">
                {(selectedAgent?.skills ?? []).filter((skill) => skill.enabled).map((skill) => (
                  <label key={skill.skill_id}>
                    <input
                      type="checkbox"
                      checked={selectedStageSkills.includes(skill.skill_id)}
                      onChange={(event) => updateSelectedSkills(event.target.checked
                        ? [...new Set([...selectedStageSkills, skill.skill_id])]
                        : selectedStageSkills.filter((id) => id !== skill.skill_id))}
                    />
                    {skill.name}
                  </label>
                ))}
                {!selectedAgent?.skills.some((skill) => skill.enabled) && (
                  <small>No enabled skills on this agent profile.</small>
                )}
              </div>
            </div>
          </>
        ) : (
          <div className="team-inspector-empty"><MousePointer2 size={20} /><span>Select a stage to edit its role and instructions.</span></div>
        )}
      </aside>
    </div>
  );
}

function TeamImportModal({
  state,
  onImported,
  onClose,
}: {
  state: TeamsState;
  onImported: (teamId: string) => void;
  onClose: () => void;
}) {
  const [file, setFile] = useState<File>();
  const [transfer, setTransfer] = useState<BundleTransfer>();
  const [progress, setProgress] = useState<TransferProgress>();
  const [environment, setEnvironment] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const teamCount = transfer?.preview?.inspection.manifest.teams?.length ?? 0;

  const close = () => {
    if (busy) return;
    if (transfer?.upload_id) void systemApi.cancelUpload(transfer.upload_id).catch(() => undefined);
    onClose();
  };

  const upload = async () => {
    if (!file) return;
    setBusy('upload');
    setError('');
    setProgress(undefined);
    try {
      const result = await systemApi.upload(file, setProgress);
      setTransfer(result);
      setEnvironment(Object.fromEntries(
        (result.preview?.missing_environment ?? []).map((key) => [key, '']),
      ));
      if (!(result.preview?.inspection.manifest.teams?.length)) {
        setError('This archive does not contain an Agent Team snapshot.');
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not inspect the Team snapshot.');
    } finally {
      setBusy('');
      setProgress(undefined);
    }
  };

  const apply = async () => {
    if (!transfer?.upload_id || !teamCount) return;
    setBusy('apply');
    setError('');
    try {
      const filled = Object.fromEntries(
        Object.entries(environment).filter(([, value]) => value.trim()),
      );
      const report: ImportReport = await systemApi.applyUpload(transfer.upload_id, filled);
      setTransfer(undefined);
      await state.refresh();
      const teamId = Object.values(report.team_id_mappings ?? {})[0];
      if (!teamId) throw new Error('The snapshot did not create a Team.');
      onImported(teamId);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not create the Team from this snapshot.');
    } finally {
      setBusy('');
    }
  };

  return (
    <div className="modal-overlay" onClick={close}>
      <div className="app-modal team-import-modal" role="dialog" aria-modal="true" aria-labelledby="team-import-title" onClick={(event) => event.stopPropagation()}>
        <div className="app-modal-head">
          <strong id="team-import-title">Create from Team snapshot</strong>
          <button className="icon-button" onClick={close} aria-label="Close Team import"><X size={17} /></button>
        </div>
        <p className="app-modal-sub">
          Import a verified Brain4All ZIP. Referenced agents are cloned with new IDs when needed; credentials are never taken from the archive.
        </p>
        <label className="profile-upload-picker">
          <FileArchive size={20} />
          <span>
            <strong>{file?.name ?? 'Choose a .zip Team snapshot'}</strong>
            <small>Checksums and safe archive limits are verified before import.</small>
          </span>
          <input
            type="file"
            accept=".zip,application/zip"
            onChange={(event) => {
              if (transfer?.upload_id) void systemApi.cancelUpload(transfer.upload_id);
              setFile(event.target.files?.[0]);
              setTransfer(undefined);
              setError('');
            }}
          />
        </label>
        {busy === 'upload' && (
          <div className="profile-transfer-progress">
            <span style={{ width: `${progress?.percent ?? 0}%` }} />
            <small>{progress?.percent ?? 0}% uploaded</small>
          </div>
        )}
        {transfer?.preview && teamCount > 0 && (
          <div className="profile-import-preview">
            <strong>{teamCount} Team snapshot ready</strong>
            <small>
              {transfer.preview.inspection.manifest.agents.length} referenced agents · {transfer.preview.inspection.files} verified files
            </small>
            {(transfer.preview.missing_environment ?? []).map((key) => (
              <label key={key}>
                {key}
                <input
                  type="password"
                  value={environment[key] ?? ''}
                  onChange={(event) => setEnvironment((current) => ({ ...current, [key]: event.target.value }))}
                  placeholder="Optional missing environment value"
                  autoComplete="off"
                />
              </label>
            ))}
          </div>
        )}
        {error && <div className="system-error" role="alert">{error}</div>}
        <div className="modal-actions">
          <button className="conn-btn ghost" onClick={close}>Cancel</button>
          {!transfer ? (
            <button className="conn-btn primary" disabled={!file || !!busy} onClick={() => void upload()}>
              <Upload size={15} /> {busy === 'upload' ? 'Uploading…' : 'Upload & inspect'}
            </button>
          ) : (
            <button className="conn-btn primary" disabled={!!busy || !teamCount} onClick={() => void apply()}>
              <Plus size={15} /> {busy === 'apply' ? 'Creating…' : 'Create Team from snapshot'}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function TeamLibrary({
  agents,
  state,
  selectedTeamId,
  setSelectedTeamId,
  selectedRunId,
  onSelectRun,
  onCreate,
  onEdit,
  onImport,
  routeAgentId,
  routeConversationId,
  onConversationNavigate,
  onOpenChat,
}: {
  agents: Agent[];
  state: TeamsState;
  selectedTeamId: string;
  setSelectedTeamId: (id: string, replace?: boolean) => void;
  selectedRunId: string;
  onSelectRun: (runId: string, replace?: boolean) => void;
  onCreate: () => void;
  onEdit: (team: Team) => void;
  onImport: () => void;
  routeAgentId?: string;
  routeConversationId?: string;
  onConversationNavigate?: (agentId: string, conversationId: string) => void;
  onOpenChat?: (agentId: string, conversationId: string) => void;
}) {
  const [task, setTask] = useState('');
  const [menuTeamId, setMenuTeamId] = useState('');
  const [renamingTeamId, setRenamingTeamId] = useState('');
  const [renameValue, setRenameValue] = useState('');
  const [exportingTeamId, setExportingTeamId] = useState('');
  const [actionError, setActionError] = useState('');

  useEffect(() => {
    if (!menuTeamId) return undefined;
    const closeOutside = (event: PointerEvent) => {
      const row = event.target instanceof Element ? event.target.closest('[data-team-row-id]') : null;
      if (row?.getAttribute('data-team-row-id') !== menuTeamId) setMenuTeamId('');
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMenuTeamId('');
    };
    document.addEventListener('pointerdown', closeOutside);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('pointerdown', closeOutside);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [menuTeamId]);
  const [removeTeam, setRemoveTeam] = useState<Team>();
  const selectedTeam = state.teams.find((team) => team.id === selectedTeamId);
  const { loadRuns } = state;

  useEffect(() => {
    if (state.status !== 'ready' || !state.teams[0]) return;
    if (!state.teams.some((team) => team.id === selectedTeamId)) setSelectedTeamId(state.teams[0].id, true);
  }, [selectedTeamId, setSelectedTeamId, state.status, state.teams]);

  useEffect(() => {
    if (selectedTeamId) void loadRuns(selectedTeamId);
  }, [selectedTeamId, loadRuns]);

  useEffect(() => {
    if (!selectedTeamId || selectedRunId || state.runsStatus !== 'ready') return;
    const latestRun = state.runs.find((candidate) => candidate.team_id === selectedTeamId);
    if (latestRun) onSelectRun(latestRun.id, true);
  }, [onSelectRun, selectedRunId, selectedTeamId, state.runs, state.runsStatus]);

  const run = async () => {
    if (!selectedTeam || !task.trim()) return;
    const workflow = (selectedTeam.workflow ?? []).map((step) => ({
      ...step,
      task: `${step.task}\n\nTeam objective: ${task.trim()}`,
    }));
    try {
      const started = await state.startRun(selectedTeam.id, task.trim(), workflow);
      if (started?.id) onSelectRun(started.id);
      setTask('');
    } catch {
      // The shared teams banner reports the API failure.
    }
  };

  const startRename = (team: Team) => {
    setMenuTeamId('');
    setRenamingTeamId(team.id);
    setRenameValue(team.name);
  };

  const commitRename = async (team: Team) => {
    const name = renameValue.trim();
    setRenamingTeamId('');
    if (!name || name === team.name) return;
    try {
      await state.rename(team.id, name);
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : 'Could not rename the Team.');
    }
  };

  const exportTeam = async (team: Team) => {
    setExportingTeamId(team.id);
    setActionError('');
    try {
      await systemApi.download([], undefined, [team.id]);
      setMenuTeamId('');
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : 'Could not export the Team snapshot.');
    } finally {
      setExportingTeamId('');
    }
  };

  const confirmRemove = async () => {
    if (!removeTeam) return;
    const teamId = removeTeam.id;
    try {
      await state.remove(teamId);
      if (selectedTeamId === teamId) {
        setSelectedTeamId(state.teams.find((team) => team.id !== teamId)?.id ?? '', true);
      }
    } finally {
      setRemoveTeam(undefined);
    }
  };

  return (
    <div className="team-library-layout">
      <aside className="team-library-list">
        <div className="teams-section-heading">
          <div><span className="teams-kicker">Your workspace</span><h2>Saved teams</h2></div>
          <span className="teams-count">{state.teams.length}</span>
        </div>
        <div className="team-create-options">
          <button className="team-new-card" onClick={onCreate}><span><Plus size={17} /></span><div><strong>Create a new team</strong><small>Design a visual workflow</small></div></button>
          <button className="team-new-card import" onClick={onImport}><span><Upload size={17} /></span><div><strong>Create from snapshot</strong><small>Import a verified Team ZIP</small></div></button>
        </div>
        {state.status === 'loading' && <p className="teams-empty-copy">Loading teams…</p>}
        {state.teams.map((team) => renamingTeamId === team.id ? (
          <div className={`team-list-item renaming ${selectedTeamId === team.id ? 'active' : ''}`} key={team.id}>
            <input
              className="team-list-rename"
              aria-label={`Rename ${team.name}`}
              value={renameValue}
              autoFocus
              onFocus={(event) => event.currentTarget.select()}
              onChange={(event) => setRenameValue(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') { event.preventDefault(); void commitRename(team); }
                if (event.key === 'Escape') { event.preventDefault(); setRenamingTeamId(''); }
              }}
              onBlur={() => void commitRename(team)}
            />
          </div>
        ) : (
          <div data-team-row-id={team.id} className={`team-list-item ${selectedTeamId === team.id ? 'active' : ''}`} key={team.id}>
            <button className="team-list-select" onClick={() => setSelectedTeamId(team.id)}>
              <span className="team-list-icon"><GitBranch size={17} /></span>
              <span><strong>{team.name}</strong><small>{team.description}</small></span>
              <ChevronRight size={15} />
            </button>
            <button
              className="team-list-more"
              aria-label={`${team.name} options`}
              aria-expanded={menuTeamId === team.id}
              onClick={(event) => {
                event.stopPropagation();
                setActionError('');
                setMenuTeamId((current) => current === team.id ? '' : team.id);
              }}
            >
              <MoreHorizontal size={15} />
            </button>
            {menuTeamId === team.id && (
              <div className="team-row-menu" role="menu">
                <button role="menuitem" onClick={() => { setMenuTeamId(''); onEdit(team); }}><GitBranch size={14} /> Edit workflow</button>
                <button role="menuitem" onClick={() => startRename(team)}><Pencil size={14} /> Rename</button>
                <button role="menuitem" disabled={exportingTeamId === team.id} onClick={() => void exportTeam(team)}>
                  <Download size={14} /> {exportingTeamId === team.id ? 'Exporting…' : 'Export'}
                </button>
                <button role="menuitem" className="danger" onClick={() => { setMenuTeamId(''); setRemoveTeam(team); }}>
                  <Trash2 size={14} /> Remove
                </button>
              </div>
            )}
          </div>
        ))}
        {actionError && <div className="teams-action-error" role="alert">{actionError}</div>}
        {state.status !== 'loading' && state.teams.length === 0 && (
          <div className="team-library-empty"><Network size={23} /><strong>No teams yet</strong><span>Create a workflow to get started.</span></div>
        )}
      </aside>

      <main className="team-library-detail">
        {selectedTeam ? (
          <>
            <TeamRunsPanel
              team={selectedTeam}
              agents={agents}
              state={state}
              task={task}
              setTask={setTask}
              onRun={run}
              requestedRunId={selectedRunId}
              onSelectRun={onSelectRun}
              routeAgentId={routeAgentId}
              routeConversationId={routeConversationId}
              onConversationNavigate={onConversationNavigate}
              onOpenChat={onOpenChat}
            />
          </>
        ) : (
          <div className="team-detail-empty"><Network size={28} /><h2>Select a team</h2><p>Choose a saved team to inspect its workflow and start a run.</p></div>
        )}
      </main>
      {removeTeam && (
        <ConfirmDialog
          title="Remove this Team?"
          message={`“${removeTeam.name}” and its saved workflow will be permanently removed. The agents in this Team will not be deleted.`}
          confirmLabel="Remove Team"
          danger
          onConfirm={() => void confirmRemove()}
          onCancel={() => setRemoveTeam(undefined)}
        />
      )}
    </div>
  );
}

export function TeamsView({
  agents,
  state,
  onClose,
  routeTeamId = '',
  routeRunId = '',
  routeCreate = false,
  routeAgentId,
  routeConversationId,
  onNavigate,
  onConversationNavigate,
  onOpenChat,
}: {
  agents: Agent[];
  state: TeamsState;
  onClose: () => void;
  routeTeamId?: string;
  routeRunId?: string;
  routeCreate?: boolean;
  onNavigate?: (teamId?: string, runId?: string, create?: boolean, replace?: boolean) => void;
  routeAgentId?: string;
  routeConversationId?: string;
  onConversationNavigate?: (agentId: string, conversationId: string) => void;
  onOpenChat?: (agentId: string, conversationId: string) => void;
}) {
  const [mode, setMode] = useState<TeamsMode>(routeCreate ? 'builder' : 'library');
  const [selectedTeamId, setSelectedTeamId] = useState(routeTeamId);
  const [selectedRunId, setSelectedRunId] = useState(routeRunId);
  const [editingTeam, setEditingTeam] = useState<Team | undefined>(() => (
    routeCreate && routeTeamId ? state.teams.find((team) => team.id === routeTeamId) : undefined
  ));
  const [importOpen, setImportOpen] = useState(false);
  const routeReady = useRef(false);

  useEffect(() => {
    if (!routeReady.current) {
      routeReady.current = true;
      return;
    }
    setMode(routeCreate ? 'builder' : 'library');
    setEditingTeam((current) => (
      routeCreate && routeTeamId && current?.id === routeTeamId ? current : undefined
    ));
    setSelectedTeamId(routeTeamId);
    setSelectedRunId(routeRunId);
  }, [routeCreate, routeRunId, routeTeamId]);

  useEffect(() => {
    if (!routeCreate || !routeTeamId || editingTeam?.id === routeTeamId) return;
    const routedTeam = state.teams.find((team) => team.id === routeTeamId);
    if (routedTeam) setEditingTeam(routedTeam);
  }, [editingTeam?.id, routeCreate, routeTeamId, state.teams]);

  const navigateLibrary = (teamId = selectedTeamId, runId = '', replace = false) => {
    setEditingTeam(undefined);
    setMode('library');
    setSelectedTeamId(teamId);
    setSelectedRunId(runId);
    onNavigate?.(teamId, runId, false, replace);
  };

  const navigateBuilder = () => {
    setEditingTeam(undefined);
    setMode('builder');
    setSelectedRunId('');
    onNavigate?.('', '', true);
  };

  const editTeam = (team: Team) => {
    setEditingTeam(team);
    setMode('builder');
    setSelectedTeamId(team.id);
    setSelectedRunId('');
    onNavigate?.(team.id, '', true);
  };

  const saved = (team: Team) => {
    navigateLibrary(team.id);
  };

  return (
    <section className="teams-view">
      <header className="teams-header">
        <div className="teams-brand">
          <span className="teams-brand-icon"><Network size={20} /></span>
          <div><h1>Agent teams</h1><p>Design, connect, and run collaborative agent workflows.</p></div>
        </div>
        <nav className="teams-mode-tabs" aria-label="Team views">
          <button className={mode === 'library' ? 'active' : ''} onClick={() => navigateLibrary()}><List size={15} /> Team library</button>
          <button className={mode === 'builder' ? 'active' : ''} onClick={navigateBuilder}><GitBranch size={15} /> Create team</button>
          <button onClick={() => setImportOpen(true)}><Upload size={15} /> Import snapshot</button>
        </nav>
        <button className="icon-button" aria-label="Close teams" onClick={onClose}><X size={19} /></button>
      </header>
      {state.error && <div className="teams-error">{state.error}</div>}
      {mode === 'builder'
        ? routeCreate && routeTeamId && !editingTeam
          ? (
            <div className="team-detail-empty">
              <Network size={28} />
              <h2>{state.status === 'loading' ? 'Loading workflow…' : 'Team not found'}</h2>
              <p>{state.status === 'loading' ? 'Restoring the team editor from this URL.' : 'This saved team is no longer available.'}</p>
              {state.status !== 'loading' && <button className="secondary-button" onClick={() => navigateLibrary('')}>Back to teams</button>}
            </div>
          )
          : (
          <TeamBuilder
            key={editingTeam?.id ?? 'new'}
            agents={agents}
            state={state}
            initialTeam={editingTeam}
            onSaved={saved}
            onBack={() => navigateLibrary()}
          />
          )
        : (
          <TeamLibrary
            agents={agents}
            state={state}
            selectedTeamId={selectedTeamId}
            setSelectedTeamId={(teamId, replace) => navigateLibrary(teamId, '', replace)}
            selectedRunId={selectedRunId}
            onSelectRun={(runId, replace) => navigateLibrary(selectedTeamId, runId, replace)}
            onCreate={navigateBuilder}
            onEdit={editTeam}
            onImport={() => setImportOpen(true)}
            routeAgentId={routeAgentId}
            routeConversationId={routeConversationId}
            onConversationNavigate={onConversationNavigate}
            onOpenChat={onOpenChat}
          />
        )}
      {importOpen && (
        <TeamImportModal
          state={state}
          onImported={(teamId) => {
            navigateLibrary(teamId);
            setImportOpen(false);
          }}
          onClose={() => setImportOpen(false)}
        />
      )}
    </section>
  );
}
