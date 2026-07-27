import { useEffect, useMemo, useRef, useState } from 'react';
import { Bell, CheckCheck, Columns3, X } from 'lucide-react';
import type { KanbanEvent } from '../types';

function eventMessage(event: KanbanEvent): string {
  const kind = event.kind.toLowerCase();
  if (event.nativeStatus === 'blocked' || kind.includes('block')) return 'Needs your attention';
  if (event.nativeStatus === 'done' || kind.includes('complete')) return 'Task completed';
  if (kind === 'created') return 'New task created';
  if (kind.includes('assign')) return event.assignee ? `Assigned to @${event.assignee}` : 'Assignment changed';
  if (kind.includes('schedule_fired')) return 'Scheduled work started';
  if (kind.includes('schedule')) return 'Schedule updated';
  if (kind.includes('start') || event.nativeStatus === 'running') return 'Agent is working on this task';
  if (kind === 'edited') return 'Task details updated';
  return kind.replaceAll('_', ' ').replace(/^\w/, (letter) => letter.toUpperCase());
}

function eventTime(value: string): string {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return 'now';
  const seconds = Math.max(0, Math.floor((Date.now() - timestamp) / 1_000));
  if (seconds < 60) return 'now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

export function KanbanNotifications({
  events,
  liveStatus,
  onOpenTask,
}: {
  events: KanbanEvent[];
  liveStatus: 'connecting' | 'live' | 'offline';
  onOpenTask: (taskId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [readIds, setReadIds] = useState<Set<number>>(() => new Set());
  const [previewId, setPreviewId] = useState<number | null>(null);
  const visibleEvents = useMemo(
    () => events.filter((event) => event.kind.toLowerCase() !== 'heartbeat'),
    [events],
  );
  const latestId = visibleEvents[0]?.id;
  const previousLatestId = useRef<number | undefined>();

  useEffect(() => {
    if (latestId == null || latestId === previousLatestId.current) return undefined;
    previousLatestId.current = latestId;
    setPreviewId(latestId);
    const timer = window.setTimeout(() => setPreviewId((current) => current === latestId ? null : current), 7_000);
    return () => window.clearTimeout(timer);
  }, [latestId]);

  const unread = useMemo(
    () => visibleEvents.filter((event) => !readIds.has(event.id)).length,
    [visibleEvents, readIds],
  );
  const preview = previewId == null
    ? null
    : visibleEvents.find((event) => event.id === previewId) ?? null;

  const markRead = (event: KanbanEvent) => {
    setReadIds((current) => new Set(current).add(event.id));
    setPreviewId(null);
  };

  const openTask = (event: KanbanEvent) => {
    markRead(event);
    setOpen(false);
    onOpenTask(event.taskId);
  };

  return (
    <div className="kanban-notifications">
      {!open && preview && (
        <div className="kanban-notification-preview" role="status">
          <button className="kanban-notification-preview-main" onClick={() => openTask(preview)}>
            <span className={`kanban-notification-icon status-${preview.status}`}>
              <Columns3 size={16} />
            </span>
            <span>
              <strong>{preview.title}</strong>
              <small>{eventMessage(preview)} · {eventTime(preview.createdAt)}</small>
            </span>
          </button>
          <button
            className="kanban-notification-dismiss"
            aria-label="Dismiss task notification"
            onClick={() => {
              markRead(preview);
              setPreviewId(null);
            }}
          >
            <X size={14} />
          </button>
        </div>
      )}

      <button
        className={`kanban-notification-bell${open ? ' active' : ''}`}
        aria-label="Task notifications"
        aria-expanded={open}
        onClick={() => {
          setOpen((current) => !current);
          setPreviewId(null);
        }}
      >
        <Bell size={18} />
        {unread > 0 && <span>{unread > 99 ? '99+' : unread}</span>}
      </button>

      {open && (
        <section className="kanban-notification-panel" aria-label="Task notifications">
          <header>
            <div>
              <strong>Task notifications</strong>
              <span className={`kanban-notification-live ${liveStatus}`}>
                <i /> {liveStatus === 'live' ? 'Live' : liveStatus === 'connecting' ? 'Connecting' : 'Reconnecting'}
              </span>
            </div>
            {unread > 0 && (
              <button
                onClick={() => setReadIds(new Set(visibleEvents.map((event) => event.id)))}
                title="Mark all as read"
              >
                <CheckCheck size={15} /> Mark read
              </button>
            )}
          </header>
          <div className="kanban-notification-list">
            {visibleEvents.length === 0 ? (
              <div className="kanban-notification-empty">
                <Bell size={20} />
                <strong>No new task activity</strong>
                <span>Agent progress and task changes will appear here.</span>
              </div>
            ) : visibleEvents.map((event) => (
              <button
                className={`kanban-notification-item${readIds.has(event.id) ? '' : ' unread'}`}
                key={event.id}
                onClick={() => openTask(event)}
              >
                <span className={`kanban-notification-icon status-${event.status}`}>
                  <Columns3 size={15} />
                </span>
                <span className="kanban-notification-copy">
                  <strong>{event.title}</strong>
                  <span>{eventMessage(event)}</span>
                  <small><code>{event.taskId}</code> · {eventTime(event.createdAt)}</small>
                </span>
                {!readIds.has(event.id) && <i className="kanban-notification-unread" />}
              </button>
            ))}
          </div>
          <button className="kanban-notification-board-link" onClick={() => {
            setOpen(false);
            onOpenTask('');
          }}>
            Open Kanban board
          </button>
        </section>
      )}
    </div>
  );
}
