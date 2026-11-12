/**
 * Pure helpers shared by the Zalo bridge and its tests.
 *
 * Keeping these free of zca-js imports lets the test suite exercise the queue,
 * cursor and request-validation rules without a live Zalo session.
 */

import { timingSafeEqual } from 'crypto';

export const MAX_QUEUE_SIZE = 100;

/** Numeric Zalo thread and message ids: 1-32 decimal digits. */
export const THREAD_ID_PATTERN = /^[0-9]{1,32}$/;

export const MAX_MESSAGE_LENGTH = 20_000;
export const MAX_CHUNK_LENGTH = 2_000;
export const MAX_URL_LENGTH = 2_000;
export const MAX_MEDIA_BYTES = 10 * 1024 * 1024;
export const MAX_MEDIA_BASE64_LENGTH = 14 * 1024 * 1024;

/** Outbound media families; zca-js picks the upload path from the extension. */
export const OUTBOUND_MEDIA_KINDS = Object.freeze(['image', 'file', 'video', 'voice']);
/** Sticker search results considered for one send. */
export const MAX_STICKER_SEARCH_LIMIT = 20;
export const MAX_STICKER_KEYWORD_LENGTH = 100;
/** Mention entries accepted in one group message. */
export const MAX_MENTIONS = 20;
/**
 * Zalo only lets the sender undo a recent message; anything older is refused
 * before the bridge calls `undo`.
 */
export const UNDO_MAX_AGE_MS = 24 * 60 * 60 * 1000;

export const READ_RATE_LIMIT_PER_MINUTE = 30;
export const SEND_RATE_LIMIT_PER_MINUTE = 20;
/** `getGroupChatHistory` is the heaviest read, so it has its own smaller bucket. */
export const GROUP_HISTORY_RATE_LIMIT_PER_MINUTE = 10;
export const LIST_CACHE_TTL_MS = 5 * 60_000;
export const GROUP_INFO_BATCH_SIZE = 50;

/** Per-call message cap shared by the history and search reads (D4). */
export const MAX_HISTORY_READ_LIMIT = 200;
export const DEFAULT_HISTORY_READ_LIMIT = 50;
export const MAX_SEARCH_QUERY_LENGTH = 200;
export const MAX_SEARCH_SCAN = 5_000;
export const MAX_BOARD_COUNT = 100;
export const MAX_GROUP_MEMBER_LIMIT = 100;
export const MAX_SEARCH_THREADS = 200;
export const MAX_PHONE_LENGTH = 15;
export const PHONE_PATTERN = /^\+?[0-9]{6,15}$/;
export const USERNAME_PATTERN = /^[A-Za-z0-9._-]{1,64}$/;
/** zca-js `BoardType.Note`. */
export const NOTE_BOARD_TYPE = 1;

/** Group utility limits shared by the poll, note, reminder and label writes. */
export const MAX_POLL_QUESTION_LENGTH = 1_000;
export const MAX_POLL_OPTIONS = 10;
export const MIN_POLL_OPTIONS = 2;
export const MAX_POLL_OPTION_LENGTH = 500;
/** Poll expiry, in hours; 0 means the poll never expires. */
export const MAX_POLL_EXPIRY_HOURS = 720;
export const MAX_NOTE_TITLE_LENGTH = 1_000;
export const MAX_REMINDER_TITLE_LENGTH = 200;
export const MAX_REMINDER_EMOJI_LENGTH = 8;
/** One year of lead time is the most a reminder accepts. */
export const MAX_REMINDER_MINUTES = 525_600;
export const MAX_LABEL_CONVERSATIONS = 500;
/** zca-js `ReminderRepeatMode` names understood by `POST /reminder-create`. */
export const REMINDER_REPEAT_MODES = Object.freeze([
  'none',
  'daily',
  'weekly',
  'monthly',
]);
export const MUTE_ACTIONS = Object.freeze(['mute', 'unmute']);
/** zca-js `MuteDuration` names understood by `POST /mute`. */
export const MUTE_DURATION_NAMES = Object.freeze([
  'one_hour',
  'four_hours',
  'forever',
  'until_8am',
]);
export const LABEL_ACTIONS = Object.freeze(['list', 'add', 'remove']);

/** Cache lifetime for a group-info lookup (single group or batch). */
export const GROUP_INFO_CACHE_TTL_MS = 10 * 60_000;

/** Group-name memory bound; the oldest name is dropped past this size. */
export const MAX_GROUP_NAMES = 1_000;

/** Minimum spacing between two upstream read-info calls. */
export const INFO_MIN_INTERVAL_MS = 1_500;
export const INFO_BASE_BACKOFF_MS = 5_000;
export const INFO_MAX_BACKOFF_MS = 300_000;

export const REACTION_NAMES = Object.freeze([
  'HEART',
  'LIKE',
  'HAHA',
  'WOW',
  'CRY',
  'ANGRY',
]);

export const THREAD_TYPES = Object.freeze(['user', 'group']);

const IMAGE_SIGNATURES = Object.freeze([
  { extension: 'jpg', mime: 'image/jpeg', bytes: [0xff, 0xd8, 0xff] },
  { extension: 'png', mime: 'image/png', bytes: [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a] },
]);

/** File extensions the dedicated media tools own. */
export const IMAGE_EXTENSIONS = Object.freeze(['jpg', 'jpeg', 'png', 'gif', 'webp']);
export const VIDEO_EXTENSIONS = Object.freeze(['mp4']);
export const VOICE_EXTENSIONS = Object.freeze(['m4a', 'mp3', 'aac', 'ogg', 'opus']);
/** A plain file name extension: letters and digits only, no dot tricks. */
export const FILE_EXTENSION_PATTERN = /^[A-Za-z0-9]{1,10}$/;

/** Zalo content types that carry an attachment. */
const MEDIA_KINDS = Object.freeze({
  'chat.photo': 'image',
  'chat.gif': 'image',
  'chat.video.msg': 'video',
  'share.file': 'file',
  'chat.voice': 'other',
  'chat.sticker': 'other',
  'chat.doodle': 'other',
  'chat.location.new': 'other',
  'chat.recommended': 'other',
  'chat.link': 'other',
});

const BASE64_PATTERN = /^[A-Za-z0-9+/]+={0,2}$/;

/**
 * Close codes that end this bridge session until the adapter restarts it.
 * 3000: another Zalo Web client opened for the same account.
 * 3003: the account was kicked from the web session.
 */
export const TERMINAL_CLOSE_REASONS = Object.freeze({
  3000: 'duplicate connection',
  3003: 'kicked',
});

export const MAX_RELOGIN_ATTEMPTS = 5;
export const RELOGIN_BASE_DELAY_MS = 60_000;

/** Stop on-demand QR login after this many consecutive expired or declined QRs. */
export const MAX_QR_EXPIRY_ATTEMPTS = 3;

/**
 * Count one expired/declined QR and decide whether another QR may be generated.
 *
 * @param {number} current
 * @returns {{attempts: number, retry: boolean}}
 */
export function nextQrExpiryAttempt(current) {
  const attempts = Math.max(0, Math.floor(Number(current) || 0)) + 1;
  return { attempts, retry: attempts < MAX_QR_EXPIRY_ATTEMPTS };
}

/** Inbound history keeps 30 days; the file is trimmed when the bridge starts. */
export const HISTORY_MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000;

/**
 * Build one history record.
 *
 * The bridge appends these as JSON lines so the Runtime tool can resolve a
 * human name to a Zalo thread and read stored history without the live queue.
 * Direct and group messages are stored, in both directions (`direction`).
 */
export function historyEntry({
  messageId,
  threadId,
  senderId,
  senderName,
  text,
  timestamp,
  threadType = 'user',
  direction = 'in',
  type = 'text',
  recovered = false,
}) {
  const parsed = Number(timestamp);
  return {
    ts: Number.isFinite(parsed) && parsed > 0 ? parsed : Date.now(),
    messageId: normalizeMessageId(messageId),
    threadId: normalizeMessageId(threadId),
    threadType: threadType === 'group' ? 'group' : 'user',
    direction: direction === 'out' ? 'out' : 'in',
    type: String(type || 'text').slice(0, 32) || 'text',
    senderId: normalizeMessageId(senderId),
    senderName: String(senderName || ''),
    text: String(text || ''),
    recovered: Boolean(recovered),
  };
}

/**
 * Parse stored JSONL history into normalized records inside the retention
 * window. Malformed lines and foreign shapes are skipped, never repaired.
 */
export function readHistoryRecords(
  lines,
  { now = Date.now(), maxAgeMs = HISTORY_MAX_AGE_MS } = {},
) {
  const cutoff = Number(now) - Math.max(0, Number(maxAgeMs) || 0);
  const records = [];
  for (const line of Array.isArray(lines) ? lines : []) {
    const raw = String(line || '').trim();
    if (!raw) {
      continue;
    }
    let record;
    try {
      record = JSON.parse(raw);
    } catch {
      continue;
    }
    if (!record || typeof record !== 'object' || Array.isArray(record)) {
      continue;
    }
    const timestamp = Number(record.ts);
    if (!Number.isFinite(timestamp) || timestamp < cutoff) {
      continue;
    }
    records.push({
      ts: timestamp,
      messageId: normalizeMessageId(record.messageId),
      threadId: normalizeMessageId(record.threadId),
      threadType: record.threadType === 'group' ? 'group' : 'user',
      direction: record.direction === 'out' ? 'out' : 'in',
      type: String(record.type || 'text').slice(0, 32) || 'text',
      senderId: normalizeMessageId(record.senderId),
      senderName: String(record.senderName || ''),
      text: String(record.text || ''),
    });
  }
  return records;
}

/**
 * Select stored records for one read or search request.
 *
 * Records are kept oldest-first so the newest `limit` entries survive; text is
 * clipped per message. Returns `{records, matched}` where `matched` counts all
 * matches before the limit, so the caller can report truncation.
 */
export function selectHistoryRecords(
  records,
  {
    threadIds = null,
    query = '',
    since = null,
    limit = DEFAULT_HISTORY_READ_LIMIT,
    textLimit = MAX_CHUNK_LENGTH,
  } = {},
) {
  const wanted = Array.isArray(threadIds)
    ? new Set(threadIds.map((id) => normalizeMessageId(id)).filter(Boolean))
    : null;
  const needle = String(query || '').trim().toLowerCase();
  const threshold = typeof since === 'number' && Number.isFinite(since) ? since : null;
  const cap = Math.max(1, Math.min(MAX_HISTORY_READ_LIMIT, Math.floor(Number(limit) || DEFAULT_HISTORY_READ_LIMIT)));
  const matches = [];
  for (const record of Array.isArray(records) ? records : []) {
    if (!record || typeof record !== 'object') {
      continue;
    }
    if (wanted && !wanted.has(normalizeMessageId(record.threadId))) {
      continue;
    }
    if (threshold !== null && !(Number(record.ts) >= threshold)) {
      continue;
    }
    if (needle && !String(record.text || '').toLowerCase().includes(needle)) {
      continue;
    }
    matches.push(record);
  }
  matches.sort((left, right) => Number(left.ts || 0) - Number(right.ts || 0));
  const selected = matches.slice(-cap).map((record) => ({
    ...record,
    text: String(record.text || '').slice(0, textLimit),
  }));
  return { records: selected, matched: matches.length };
}

/** True when `value` is one usable numeric Zalo id. */
function numericId(value) {
  if (typeof value !== 'string' && typeof value !== 'number') {
    return '';
  }
  const id = normalizeMessageId(value);
  return THREAD_ID_PATTERN.test(id) ? id : '';
}

function optionalSinceHours(value) {
  if (value === undefined || value === null) {
    return { ok: true, value: null };
  }
  const number = Number(value);
  if (!Number.isFinite(number) || number < 0 || number > 8_760) {
    return { ok: false, error: 'invalid_request' };
  }
  return { ok: true, value: number };
}

function boundedInteger(value, { min = 1, max = MAX_HISTORY_READ_LIMIT, fallback }) {
  if (value === undefined || value === null) {
    return fallback;
  }
  if (typeof value !== 'number' || !Number.isInteger(value) || value < min || value > max) {
    return null;
  }
  return value;
}

/** Validate a `POST /history` body (direct-message history). */
export function validateHistoryBody(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return { ok: false, error: 'invalid_request' };
  }
  const chatId = numericId(body.chatId);
  if (!chatId) {
    return { ok: false, error: 'invalid_request' };
  }
  const limit = boundedInteger(body.limit, { fallback: DEFAULT_HISTORY_READ_LIMIT });
  if (limit === null) {
    return { ok: false, error: 'invalid_request' };
  }
  const since = optionalSinceHours(body.sinceHours);
  if (!since.ok) {
    return since;
  }
  if (body.backfill !== undefined && typeof body.backfill !== 'boolean') {
    return { ok: false, error: 'invalid_request' };
  }
  return {
    ok: true,
    chatId,
    limit,
    sinceHours: since.value,
    backfill: body.backfill !== false,
  };
}

/** Validate a `POST /group-history` body. */
export function validateGroupHistoryBody(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return { ok: false, error: 'invalid_request' };
  }
  const groupId = numericId(body.groupId);
  if (!groupId) {
    return { ok: false, error: 'invalid_request' };
  }
  const limit = boundedInteger(body.limit, { fallback: DEFAULT_HISTORY_READ_LIMIT });
  if (limit === null) {
    return { ok: false, error: 'invalid_request' };
  }
  return { ok: true, groupId, limit };
}

/** Validate a `POST /search` body over stored history. */
export function validateSearchBody(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return { ok: false, error: 'invalid_request' };
  }
  if (typeof body.query !== 'string') {
    return { ok: false, error: 'invalid_request' };
  }
  const query = body.query.trim();
  if (!query || query.length > MAX_SEARCH_QUERY_LENGTH) {
    return { ok: false, error: 'invalid_request' };
  }
  const limit = boundedInteger(body.limit, { fallback: DEFAULT_HISTORY_READ_LIMIT });
  if (limit === null) {
    return { ok: false, error: 'invalid_request' };
  }
  const since = optionalSinceHours(body.sinceHours);
  if (!since.ok) {
    return since;
  }
  let chatIds = null;
  if (body.chatIds !== undefined && body.chatIds !== null) {
    if (!Array.isArray(body.chatIds) || body.chatIds.length > MAX_SEARCH_THREADS) {
      return { ok: false, error: 'invalid_request' };
    }
    chatIds = [];
    for (const value of body.chatIds) {
      const id = numericId(value);
      if (!id) {
        return { ok: false, error: 'invalid_request' };
      }
      chatIds.push(id);
    }
  }
  return { ok: true, query, chatIds, limit, sinceHours: since.value };
}

/** Validate a `POST /group-info` body. */
export function validateGroupInfoBody(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return { ok: false, error: 'invalid_request' };
  }
  const groupId = numericId(body.groupId);
  if (!groupId) {
    return { ok: false, error: 'invalid_request' };
  }
  const memberLimit = boundedInteger(body.memberLimit, {
    min: 0,
    max: MAX_GROUP_MEMBER_LIMIT,
    fallback: GROUP_INFO_BATCH_SIZE,
  });
  if (memberLimit === null) {
    return { ok: false, error: 'invalid_request' };
  }
  return { ok: true, groupId, memberLimit };
}

/**
 * Validate a `POST /user-info` body.
 *
 * Exactly one lookup key is accepted: numeric id, phone number, or username.
 */
export function validateUserInfoBody(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return { ok: false, error: 'invalid_request' };
  }
  if (Object.keys(body).some((key) => !['userId', 'phone', 'username'].includes(key))) {
    return { ok: false, error: 'invalid_request' };
  }
  const provided = ['userId', 'phone', 'username'].filter((field) => {
    const value = body[field];
    return value !== undefined && value !== null && value !== '';
  });
  if (provided.length !== 1) {
    return { ok: false, error: 'invalid_request' };
  }
  const field = provided[0];
  const raw = body[field];
  if (typeof raw !== 'string') {
    return { ok: false, error: 'invalid_request' };
  }
  const value = raw.trim();
  if (field === 'userId' && !numericId(value)) {
    return { ok: false, error: 'invalid_request' };
  }
  if (field === 'phone' && (!PHONE_PATTERN.test(value) || value.length > MAX_PHONE_LENGTH + 1)) {
    return { ok: false, error: 'invalid_request' };
  }
  if (field === 'username' && !USERNAME_PATTERN.test(value)) {
    return { ok: false, error: 'invalid_request' };
  }
  return { ok: true, field, value };
}

/** Validate a reminders or notes listing body. */
export function validateBoardBody(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return { ok: false, error: 'invalid_request' };
  }
  const groupId = numericId(body.groupId);
  if (!groupId) {
    return { ok: false, error: 'invalid_request' };
  }
  const page = boundedInteger(body.page, { min: 1, max: 10_000, fallback: 1 });
  if (page === null) {
    return { ok: false, error: 'invalid_request' };
  }
  const count = boundedInteger(body.count, { min: 1, max: MAX_BOARD_COUNT, fallback: 20 });
  if (count === null) {
    return { ok: false, error: 'invalid_request' };
  }
  return { ok: true, groupId, page, count };
}

/** Validate a `POST /poll-detail` body. */
export function validatePollDetailBody(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return { ok: false, error: 'invalid_request' };
  }
  const groupId = numericId(body.groupId);
  const pollId = numericId(
    typeof body.pollId === 'number' ? String(body.pollId) : body.pollId,
  );
  if (!groupId || !pollId) {
    return { ok: false, error: 'invalid_request' };
  }
  return { ok: true, groupId, pollId };
}

/**
 * Drop malformed or expired JSONL history records.
 *
 * Returns the surviving raw lines so the caller can rewrite the file without
 * re-serializing (and potentially altering) records it did not create.
 */
export function trimHistoryLines(lines, { now = Date.now(), maxAgeMs = HISTORY_MAX_AGE_MS } = {}) {
  if (!Array.isArray(lines)) {
    return [];
  }
  const cutoff = Number(now) - Math.max(0, Number(maxAgeMs) || 0);
  const kept = [];
  for (const line of lines) {
    const raw = String(line || '').trim();
    if (!raw) {
      continue;
    }
    let record;
    try {
      record = JSON.parse(raw);
    } catch {
      continue;
    }
    const timestamp = Number(record?.ts);
    if (!Number.isFinite(timestamp) || timestamp < cutoff) {
      continue;
    }
    kept.push(raw);
  }
  return kept;
}

/**
 * Classify a listener close code.
 *
 * Terminal codes must not trigger an automatic relogin: opening Zalo Web again
 * or fighting a kick would keep closing the session. The adapter's reconnect
 * restarts the whole bridge instead.
 */
export function classifyCloseCode(code) {
  const numeric = Number(code);
  const reason = TERMINAL_CLOSE_REASONS[String(numeric)];
  if (reason) {
    return { terminal: true, code: numeric, reason };
  }
  return { terminal: false, code: numeric, reason: '' };
}

/**
 * Linear relogin delay for attempt 1..MAX_RELOGIN_ATTEMPTS.
 */
export function reloginDelayMs(attempt) {
  const numeric = Math.max(1, Number(attempt) || 1);
  return RELOGIN_BASE_DELAY_MS * numeric;
}

/**
 * Serialize a tough-cookie CookieJar (or an already-serialized list) into the
 * cookie array shape accepted by `zalo.login({ cookie, imei, userAgent })`.
 *
 * `getCookie()` returns a CookieJar, while `login()` accepts a cookie array or
 * `{ cookies }`; `CookieJar.toJSON().cookies` is the same shape loginQR
 * persists.
 */
export function cookieListFromJar(jar) {
  if (!jar) {
    return null;
  }
  if (Array.isArray(jar)) {
    return jar;
  }
  if (typeof jar.toJSON === 'function') {
    try {
      const serialized = jar.toJSON();
      if (serialized && Array.isArray(serialized.cookies)) {
        return serialized.cookies;
      }
    } catch {
      return null;
    }
  }
  if (Array.isArray(jar.cookies)) {
    return jar.cookies;
  }
  return null;
}

/**
 * Append an item to a bounded queue, dropping the oldest entries.
 *
 * @param {Array} queue
 * @param {*} item
 * @param {number} maxSize
 * @returns {Array}
 */
export function pushToQueue(queue, item, maxSize = MAX_QUEUE_SIZE) {
  if (!Array.isArray(queue)) {
    throw new TypeError('queue must be an array');
  }
  queue.push(item);
  while (queue.length > maxSize) {
    queue.shift();
  }
  return queue;
}

/**
 * Coerce a Zalo message id (string or number) to a comparable string.
 */
export function normalizeMessageId(value) {
  if (value === null || value === undefined) {
    return '';
  }
  return String(value).trim();
}

/**
 * True when `candidate` is newer than `cursor`.
 *
 * Zalo message ids are normally decimal strings, so compare them numerically
 * with BigInt (they can exceed Number.MAX_SAFE_INTEGER). Non-numeric ids fall
 * back to a plain inequality check.
 */
export function isNewerMessageId(candidate, cursor) {
  const next = normalizeMessageId(candidate);
  const last = normalizeMessageId(cursor);
  if (!next) {
    return false;
  }
  if (!last) {
    return true;
  }
  if (/^\d+$/.test(next) && /^\d+$/.test(last)) {
    try {
      return BigInt(next) > BigInt(last);
    } catch {
      return false;
    }
  }
  return next !== last;
}

/**
 * Advance a `{threadId: lastMessageId}` cursor when the id is newer.
 *
 * @returns {Record<string, string>} the same cursor object
 */
export function rememberCursor(cursor, threadId, messageId) {
  const thread = normalizeMessageId(threadId);
  const message = normalizeMessageId(messageId);
  if (!thread || !message) {
    return cursor;
  }
  if (isNewerMessageId(message, cursor[thread])) {
    cursor[thread] = message;
  }
  return cursor;
}

/**
 * Decide whether an inbound message should enter the bridge queue.
 *
 * Live messages only need the seen-id dedupe. Catch-up (old) messages also
 * require an id newer than the persisted per-thread cursor.
 *
 * @param {{cursor: Record<string, string>, seenIds: Set<string>, threadId: string,
 *          messageId: string, requireNewer?: boolean}} input
 * @returns {boolean}
 */
export function shouldQueueMessage({
  cursor,
  seenIds,
  threadId,
  messageId,
  requireNewer = false,
}) {
  const id = normalizeMessageId(messageId);
  if (!id || !seenIds || typeof seenIds.has !== 'function') {
    return false;
  }
  if (seenIds.has(id)) {
    return false;
  }
  if (requireNewer) {
    const thread = normalizeMessageId(threadId);
    if (!thread || !isNewerMessageId(id, cursor?.[thread])) {
      return false;
    }
  }
  return true;
}

/**
 * True when an HTTP peer address is loopback (IPv4, IPv6 or mapped IPv6).
 */
export function isLoopback(remoteAddress) {
  const raw = String(remoteAddress || '').trim().toLowerCase();
  if (!raw) {
    return false;
  }
  if (raw === '::1' || raw === '127.0.0.1') {
    return true;
  }
  const mapped = raw.match(/^::ffff:(\d{1,3}(?:\.\d{1,3}){3})$/);
  if (mapped) {
    return mapped[1] === '127.0.0.1';
  }
  return false;
}

/**
 * Validate a `POST /send` JSON body.
 *
 * Legacy helper kept for the existing test surface; the bridge routes use
 * `validateSendMessageBody`, which also returns `threadType`.
 *
 * @returns {{ok: true, chatId: string, message: string} |
 *           {ok: false, error: string}}
 */
export function validateSendBody(body) {
  const request = validateSendMessageBody(body);
  if (!request.ok) {
    return {
      ok: false,
      error: 'chatId must be numeric and message must be 1..20000 characters',
    };
  }
  return { ok: true, chatId: request.chatId, message: request.message };
}

/**
 * True when `value` is a numeric Zalo id accepted by the bridge contract.
 */
export function isValidThreadId(value) {
  return typeof value === 'string' && THREAD_ID_PATTERN.test(value);
}

/**
 * Resolve the optional `threadType` field; the default is a direct message.
 *
 * @returns {'user' | 'group' | ''} empty when the value is not supported
 */
export function parseThreadType(value) {
  if (value === undefined || value === null || value === '') {
    return 'user';
  }
  return THREAD_TYPES.includes(value) ? value : '';
}

/**
 * Validate `chatId` and `threadType` shared by every send/read endpoint.
 *
 * @returns {{ok: true, chatId: string, threadType: string} |
 *           {ok: false, error: string}}
 */
export function validateThreadTarget(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return { ok: false, error: 'invalid_request' };
  }
  const chatId = typeof body.chatId === 'string' ? body.chatId.trim() : '';
  if (!isValidThreadId(chatId)) {
    return { ok: false, error: 'invalid_request' };
  }
  const threadType = parseThreadType(body.threadType);
  if (!threadType) {
    return { ok: false, error: 'invalid_request' };
  }
  return { ok: true, chatId, threadType };
}

function isValidMessageText(message) {
  return (
    typeof message === 'string' &&
    message.length > 0 &&
    message.length <= MAX_MESSAGE_LENGTH &&
    message.trim().length > 0
  );
}

/** Validate a `POST /send` body, including `threadType` and reply options. */
export function validateSendMessageBody(body) {
  const target = validateThreadTarget(body);
  if (!target.ok) {
    return target;
  }
  if (!isValidMessageText(body.message)) {
    return { ok: false, error: 'invalid_request' };
  }
  let quoteMessageId = '';
  if (body.quoteMessageId !== undefined && body.quoteMessageId !== null) {
    const id = typeof body.quoteMessageId === 'string' ? body.quoteMessageId.trim() : '';
    if (!isValidThreadId(id)) {
      return { ok: false, error: 'invalid_request' };
    }
    quoteMessageId = id;
  }
  let mentions = [];
  if (body.mentions !== undefined && body.mentions !== null) {
    if (
      target.threadType !== 'group' ||
      !Array.isArray(body.mentions) ||
      body.mentions.length === 0 ||
      body.mentions.length > MAX_MENTIONS
    ) {
      return { ok: false, error: 'invalid_request' };
    }
    for (const value of body.mentions) {
      const id = typeof value === 'string' ? value.trim() : '';
      if (!isValidThreadId(id) || mentions.includes(id)) {
        return { ok: false, error: 'invalid_request' };
      }
      mentions.push(id);
    }
  }
  return {
    ok: true,
    chatId: target.chatId,
    threadType: target.threadType,
    message: body.message,
    quoteMessageId,
    mentions,
  };
}

/**
 * Detect an allowed image by its magic bytes.
 *
 * @param {Buffer | Uint8Array} buffer
 * @returns {{mime: string, extension: string} | null}
 */
export function detectImageType(buffer) {
  if (!buffer || typeof buffer.length !== 'number') {
    return null;
  }
  for (const signature of IMAGE_SIGNATURES) {
    if (buffer.length < signature.bytes.length) {
      continue;
    }
    if (signature.bytes.every((byte, index) => buffer[index] === byte)) {
      return { mime: signature.mime, extension: signature.extension };
    }
  }
  if (
    buffer.length >= 12 &&
    buffer[0] === 0x52 &&
    buffer[1] === 0x49 &&
    buffer[2] === 0x46 &&
    buffer[3] === 0x46 &&
    buffer[8] === 0x57 &&
    buffer[9] === 0x45 &&
    buffer[10] === 0x42 &&
    buffer[11] === 0x50
  ) {
    return { mime: 'image/webp', extension: 'webp' };
  }
  if (
    buffer.length >= 6 &&
    buffer[0] === 0x47 &&
    buffer[1] === 0x49 &&
    buffer[2] === 0x46 &&
    buffer[3] === 0x38 &&
    (buffer[4] === 0x37 || buffer[4] === 0x39) &&
    buffer[5] === 0x61
  ) {
    return { mime: 'image/gif', extension: 'gif' };
  }
  return null;
}

/**
 * Reduce a client file name to a safe basename with the detected extension.
 */
export function safeImageFileName(name, extension) {
  return safeMediaFileName(name, extension, 'image');
}

/**
 * Reduce a client file name to a safe basename with one fixed extension.
 *
 * The extension is always the validated one, never whatever the caller sent.
 */
export function safeMediaFileName(name, extension, fallback = 'file') {
  const raw = typeof name === 'string' ? name.split(/[\\/]/).pop() : '';
  const cleaned = String(raw || '')
    .replace(/[\u0000-\u001f\u007f]/g, '')
    .trim()
    .slice(0, 100);
  const baseName = `${fallback}.${extension}`;
  if (!cleaned || cleaned === '.' || cleaned === '..') {
    return baseName;
  }
  const dot = cleaned.lastIndexOf('.');
  const base = (dot > 0 ? cleaned.slice(0, dot) : cleaned).trim();
  if (!base || base === '.' || base === '..') {
    return baseName;
  }
  return `${base}.${extension}`;
}

/** Lowercase extension of a client file name, or an empty string. */
export function fileExtension(name) {
  const raw = typeof name === 'string' ? name.split(/[\\/]/).pop() : '';
  const dot = String(raw || '').lastIndexOf('.');
  if (dot <= 0) {
    return '';
  }
  return String(raw).slice(dot + 1).toLowerCase();
}

/** True for an MP4 container (ISO base media `ftyp` box). */
export function isMp4(buffer) {
  return (
    Boolean(buffer) &&
    typeof buffer.length === 'number' &&
    buffer.length >= 12 &&
    buffer[4] === 0x66 &&
    buffer[5] === 0x74 &&
    buffer[6] === 0x79 &&
    buffer[7] === 0x70
  );
}

/**
 * Detect a supported audio container by its magic bytes.
 *
 * @returns {'m4a' | 'mp3' | 'aac' | 'ogg' | ''}
 */
export function detectAudioType(buffer) {
  if (!buffer || typeof buffer.length !== 'number') {
    return '';
  }
  if (isMp4(buffer)) {
    return 'm4a';
  }
  if (buffer.length >= 3 && buffer[0] === 0x49 && buffer[1] === 0x44 && buffer[2] === 0x33) {
    return 'mp3';
  }
  if (
    buffer.length >= 4 &&
    buffer[0] === 0x4f &&
    buffer[1] === 0x67 &&
    buffer[2] === 0x67 &&
    buffer[3] === 0x53
  ) {
    return 'ogg';
  }
  if (buffer.length >= 2 && buffer[0] === 0xff) {
    // ADTS AAC uses 0xFFF1/0xFFF9; MPEG audio frames use 0xFFEx.
    const second = buffer[1];
    if ((second & 0xf6) === 0xf0) {
      return 'aac';
    }
    if ((second & 0xe0) === 0xe0) {
      return 'mp3';
    }
  }
  return '';
}

/**
 * Check one media payload against its declared family.
 *
 * @returns {{ok: true, fileName: string, mimeType: string} |
 *           {ok: false, error: string}}
 */
function mediaPayload(kind, name, buffer) {
  const extension = fileExtension(name);
  if (kind === 'image') {
    const detected = detectImageType(buffer);
    if (!detected) {
      return { ok: false, error: 'invalid_request' };
    }
    return {
      ok: true,
      fileName: safeMediaFileName(name, detected.extension, 'image'),
      mimeType: detected.mime,
    };
  }
  if (kind === 'video') {
    if (!VIDEO_EXTENSIONS.includes(extension) || !isMp4(buffer)) {
      return { ok: false, error: 'invalid_request' };
    }
    return {
      ok: true,
      fileName: safeMediaFileName(name, extension, 'video'),
      mimeType: 'video/mp4',
    };
  }
  if (kind === 'voice') {
    const detected = detectAudioType(buffer);
    const compatible =
      detected === extension || (detected === 'ogg' && extension === 'opus');
    if (!VOICE_EXTENSIONS.includes(extension) || !detected || !compatible) {
      return { ok: false, error: 'invalid_request' };
    }
    return {
      ok: true,
      fileName: safeMediaFileName(name, extension, 'voice'),
      mimeType: `audio/${extension === 'opus' ? 'opus' : extension}`,
    };
  }
  // A plain file never reuses an image, video or voice extension.
  if (
    !FILE_EXTENSION_PATTERN.test(extension) ||
    IMAGE_EXTENSIONS.includes(extension) ||
    VIDEO_EXTENSIONS.includes(extension) ||
    VOICE_EXTENSIONS.includes(extension) ||
    detectImageType(buffer) ||
    isMp4(buffer) ||
    detectAudioType(buffer)
  ) {
    return { ok: false, error: 'invalid_request' };
  }
  return {
    ok: true,
    fileName: safeMediaFileName(name, extension, 'file'),
    mimeType: 'application/octet-stream',
  };
}

/** Validate a `POST /send-media` body and decode the media bytes. */
export function validateSendMediaBody(body) {
  const target = validateThreadTarget(body);
  if (!target.ok) {
    return target;
  }
  const kind = body.kind === undefined || body.kind === null ? 'image' : body.kind;
  if (!OUTBOUND_MEDIA_KINDS.includes(kind)) {
    return { ok: false, error: 'invalid_request' };
  }
  if (typeof body.fileName !== 'string' || !body.fileName.trim()) {
    return { ok: false, error: 'invalid_request' };
  }
  if (typeof body.caption !== 'undefined') {
    if (typeof body.caption !== 'string' || body.caption.length > MAX_MESSAGE_LENGTH) {
      return { ok: false, error: 'invalid_request' };
    }
  }
  const encoded = body.contentBase64;
  if (
    typeof encoded !== 'string' ||
    !encoded ||
    encoded.length > MAX_MEDIA_BASE64_LENGTH ||
    !BASE64_PATTERN.test(encoded)
  ) {
    return { ok: false, error: 'invalid_request' };
  }
  const buffer = Buffer.from(encoded, 'base64');
  if (!buffer.length || buffer.length > MAX_MEDIA_BYTES) {
    return { ok: false, error: 'invalid_request' };
  }
  const payload = mediaPayload(kind, body.fileName, buffer);
  if (!payload.ok) {
    return payload;
  }
  return {
    ok: true,
    chatId: target.chatId,
    threadType: target.threadType,
    kind,
    fileName: payload.fileName,
    caption: typeof body.caption === 'string' ? body.caption : '',
    mimeType: payload.mimeType,
    buffer,
  };
}

/** True for an absolute http/https URL within the length cap. */
export function isHttpUrl(value) {
  if (typeof value !== 'string' || !value || value.length > MAX_URL_LENGTH) {
    return false;
  }
  try {
    const parsed = new URL(value);
    return parsed.protocol === 'http:' || parsed.protocol === 'https:';
  } catch {
    return false;
  }
}

/** Validate a `POST /send-link` body. */
export function validateSendLinkBody(body) {
  const target = validateThreadTarget(body);
  if (!target.ok) {
    return target;
  }
  const url = typeof body.url === 'string' ? body.url.trim() : '';
  if (!isHttpUrl(url)) {
    return { ok: false, error: 'invalid_request' };
  }
  const message = typeof body.message === 'undefined' ? '' : body.message;
  if (typeof message !== 'string' || message.length > MAX_MESSAGE_LENGTH) {
    return { ok: false, error: 'invalid_request' };
  }
  return {
    ok: true,
    chatId: target.chatId,
    threadType: target.threadType,
    url,
    message,
  };
}

/** Validate a `POST /send-sticker` body. */
export function validateSendStickerBody(body) {
  const target = validateThreadTarget(body);
  if (!target.ok) {
    return target;
  }
  const keyword = typeof body.keyword === 'string' ? body.keyword.trim() : '';
  if (!keyword || keyword.length > MAX_STICKER_KEYWORD_LENGTH) {
    return { ok: false, error: 'invalid_request' };
  }
  const index = boundedInteger(body.index, {
    min: 0,
    max: MAX_STICKER_SEARCH_LIMIT - 1,
    fallback: 0,
  });
  if (index === null) {
    return { ok: false, error: 'invalid_request' };
  }
  return {
    ok: true,
    chatId: target.chatId,
    threadType: target.threadType,
    keyword,
    index,
  };
}

/** Validate a `POST /send-card` body. */
export function validateSendCardBody(body) {
  const target = validateThreadTarget(body);
  if (!target.ok) {
    return target;
  }
  const userId = typeof body.userId === 'string' ? body.userId.trim() : '';
  if (!isValidThreadId(userId)) {
    return { ok: false, error: 'invalid_request' };
  }
  let phoneNumber = '';
  if (body.phoneNumber !== undefined && body.phoneNumber !== null && body.phoneNumber !== '') {
    if (typeof body.phoneNumber !== 'string') {
      return { ok: false, error: 'invalid_request' };
    }
    phoneNumber = body.phoneNumber.trim();
    if (!PHONE_PATTERN.test(phoneNumber) || phoneNumber.length > MAX_PHONE_LENGTH + 1) {
      return { ok: false, error: 'invalid_request' };
    }
  }
  return {
    ok: true,
    chatId: target.chatId,
    threadType: target.threadType,
    userId,
    phoneNumber,
  };
}

/** Validate a `POST /forward` body: one stored source message, one target. */
export function validateForwardBody(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return { ok: false, error: 'invalid_request' };
  }
  const sourceChatId = typeof body.sourceChatId === 'string' ? body.sourceChatId.trim() : '';
  const sourceThreadType = parseThreadType(body.sourceThreadType);
  if (!isValidThreadId(sourceChatId) || !sourceThreadType) {
    return { ok: false, error: 'invalid_request' };
  }
  const messageId = typeof body.messageId === 'string' ? body.messageId.trim() : '';
  if (!isValidThreadId(messageId)) {
    return { ok: false, error: 'invalid_request' };
  }
  const target = validateThreadTarget(body);
  if (!target.ok) {
    return target;
  }
  return {
    ok: true,
    sourceChatId,
    sourceThreadType,
    messageId,
    chatId: target.chatId,
    threadType: target.threadType,
  };
}

/** Validate a `POST /undo` body. */
export function validateUndoBody(body) {
  const target = validateThreadTarget(body);
  if (!target.ok) {
    return target;
  }
  const messageId = typeof body.messageId === 'string' ? body.messageId.trim() : '';
  if (!isValidThreadId(messageId)) {
    return { ok: false, error: 'invalid_request' };
  }
  return {
    ok: true,
    chatId: target.chatId,
    threadType: target.threadType,
    messageId,
  };
}

/**
 * Build zca-js mention entries from the message text.
 *
 * Every mention must already appear as `@DisplayName` in the text; the bridge
 * only validates and locates it, so a send can never silently rewrite the
 * message. Returns `{ok: false}` when a name is missing.
 */
export function mentionPayloads(text, members) {
  const message = typeof text === 'string' ? text : '';
  const entries = [];
  const spans = [];
  for (const member of Array.isArray(members) ? members : []) {
    const uid = normalizeMessageId(member?.uid);
    const name = String(member?.name || '');
    if (!uid || !name) {
      return { ok: false, error: 'invalid_request' };
    }
    const token = `@${name}`;
    let position = message.indexOf(token);
    while (position >= 0) {
      const overlaps = spans.some(
        (span) => position < span.end && position + token.length > span.start,
      );
      if (!overlaps) {
        break;
      }
      position = message.indexOf(token, position + 1);
    }
    if (position < 0) {
      return { ok: false, error: 'invalid_request' };
    }
    spans.push({ start: position, end: position + token.length });
    entries.push({ pos: position, uid, len: token.length });
  }
  return { ok: true, mentions: entries };
}

/** True when a stored message is too old for Zalo's undo window. */
export function undoExpired(timestamp, { now = Date.now() } = {}) {
  const parsed = Number(timestamp);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return true;
  }
  return Number(now) - parsed > UNDO_MAX_AGE_MS;
}

/**
 * Shape the quote object zca-js needs from one remembered message reference.
 */
export function quotePayload(reference) {
  if (!reference || typeof reference !== 'object') {
    return null;
  }
  const msgId = normalizeMessageId(reference.msgId);
  const cliMsgId = normalizeMessageId(reference.cliMsgId);
  if (!msgId || !cliMsgId) {
    return null;
  }
  return {
    content: reference.content,
    msgType: String(reference.msgType || 'webchat'),
    propertyExt: reference.propertyExt,
    uidFrom: normalizeMessageId(reference.uidFrom),
    msgId,
    cliMsgId,
    ts: Number(reference.ts) || 0,
    ttl: Number(reference.ttl) || 0,
  };
}

/**
 * Remember the fields of one live message so it can be quoted or undone later.
 *
 * Only identity and reply metadata are kept; the bridge never persists this in
 * a log.
 */
export function messageRef(data, { threadId, threadType, isSelf = false } = {}) {
  if (!data || typeof data !== 'object' || Array.isArray(data)) {
    return null;
  }
  const msgId = normalizeMessageId(data.msgId ?? data.realMsgId);
  const cliMsgId = normalizeMessageId(data.cliMsgId);
  const thread = normalizeMessageId(threadId);
  if (!msgId || !cliMsgId || !thread) {
    return null;
  }
  return {
    msgId,
    cliMsgId,
    threadId: thread,
    threadType: threadType === 'group' ? 'group' : 'user',
    uidFrom: normalizeMessageId(data.uidFrom),
    msgType: String(data.msgType || ''),
    ts: Number(data.ts) || 0,
    ttl: Number(data.ttl) || 0,
    content: data.content,
    propertyExt: data.propertyExt,
    isSelf: Boolean(isSelf),
  };
}

/** Validate a `POST /react` body. */
export function validateReactBody(body) {
  const target = validateThreadTarget(body);
  if (!target.ok) {
    return target;
  }
  const messageId = typeof body.messageId === 'string' ? body.messageId.trim() : '';
  if (!isValidThreadId(messageId)) {
    return { ok: false, error: 'invalid_request' };
  }
  const reaction = typeof body.reaction === 'string' ? body.reaction.trim() : '';
  if (!REACTION_NAMES.includes(reaction)) {
    return { ok: false, error: 'invalid_request' };
  }
  return {
    ok: true,
    chatId: target.chatId,
    threadType: target.threadType,
    messageId,
    reaction,
  };
}

/** Validate a `POST /typing` body. */
export function validateTypingBody(body) {
  return validateThreadTarget(body);
}

/** Validate a `POST /seen` body. */
export function validateSeenBody(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return { ok: false, error: 'invalid_request' };
  }
  const messageId = typeof body.messageId === 'string' ? body.messageId.trim() : '';
  if (!isValidThreadId(messageId)) {
    return { ok: false, error: 'invalid_request' };
  }
  return { ok: true, messageId };
}

function optionalBoolean(body, field, fallback = false) {
  if (body[field] === undefined || body[field] === null) {
    return fallback;
  }
  if (typeof body[field] !== 'boolean') {
    return null;
  }
  return body[field];
}

function trimmedText(value, maxLength) {
  if (typeof value !== 'string') {
    return '';
  }
  const text = value.trim();
  if (!text || text.length > maxLength) {
    return '';
  }
  return text;
}

/** Validate a `POST /poll-create` body. */
export function validatePollCreateBody(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return { ok: false, error: 'invalid_request' };
  }
  const groupId = numericId(body.groupId);
  if (!groupId) {
    return { ok: false, error: 'invalid_request' };
  }
  const question = trimmedText(body.question, MAX_POLL_QUESTION_LENGTH);
  if (!question) {
    return { ok: false, error: 'invalid_request' };
  }
  if (
    !Array.isArray(body.options) ||
    body.options.length < MIN_POLL_OPTIONS ||
    body.options.length > MAX_POLL_OPTIONS
  ) {
    return { ok: false, error: 'invalid_request' };
  }
  const options = [];
  const seen = new Set();
  for (const value of body.options) {
    const option = trimmedText(value, MAX_POLL_OPTION_LENGTH);
    if (!option) {
      return { ok: false, error: 'invalid_request' };
    }
    const key = option.toLowerCase();
    if (seen.has(key)) {
      return { ok: false, error: 'invalid_request' };
    }
    seen.add(key);
    options.push(option);
  }
  const allowMultiChoices = optionalBoolean(body, 'allowMultiChoices');
  const allowAddNewOption = optionalBoolean(body, 'allowAddNewOption');
  const hideVotePreview = optionalBoolean(body, 'hideVotePreview');
  const isAnonymous = optionalBoolean(body, 'isAnonymous');
  if (
    allowMultiChoices === null ||
    allowAddNewOption === null ||
    hideVotePreview === null ||
    isAnonymous === null
  ) {
    return { ok: false, error: 'invalid_request' };
  }
  const expiresInHours = boundedInteger(body.expiresInHours, {
    min: 0,
    max: MAX_POLL_EXPIRY_HOURS,
    fallback: 0,
  });
  if (expiresInHours === null) {
    return { ok: false, error: 'invalid_request' };
  }
  return {
    ok: true,
    groupId,
    question,
    options,
    allowMultiChoices,
    allowAddNewOption,
    hideVotePreview,
    isAnonymous,
    expiresInHours,
  };
}

/** Validate a `POST /note-create` body. */
export function validateNoteCreateBody(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return { ok: false, error: 'invalid_request' };
  }
  const groupId = numericId(body.groupId);
  if (!groupId) {
    return { ok: false, error: 'invalid_request' };
  }
  const title = trimmedText(body.title, MAX_NOTE_TITLE_LENGTH);
  if (!title) {
    return { ok: false, error: 'invalid_request' };
  }
  const pinned = optionalBoolean(body, 'pinned');
  if (pinned === null) {
    return { ok: false, error: 'invalid_request' };
  }
  return { ok: true, groupId, title, pinned };
}

/** Validate a `POST /reminder-create` body. */
export function validateReminderCreateBody(body) {
  const target = validateThreadTarget(body);
  if (!target.ok) {
    return target;
  }
  const title = trimmedText(body.title, MAX_REMINDER_TITLE_LENGTH);
  if (!title) {
    return { ok: false, error: 'invalid_request' };
  }
  const startsInMinutes = boundedInteger(body.startsInMinutes, {
    min: 1,
    max: MAX_REMINDER_MINUTES,
    fallback: null,
  });
  if (startsInMinutes === null) {
    return { ok: false, error: 'invalid_request' };
  }
  let emoji = '';
  if (body.emoji !== undefined && body.emoji !== null) {
    if (
      typeof body.emoji !== 'string' ||
      body.emoji.length > MAX_REMINDER_EMOJI_LENGTH
    ) {
      return { ok: false, error: 'invalid_request' };
    }
    emoji = body.emoji.trim();
  }
  const repeat =
    body.repeat === undefined || body.repeat === null ? 'none' : body.repeat;
  if (!REMINDER_REPEAT_MODES.includes(repeat)) {
    return { ok: false, error: 'invalid_request' };
  }
  return {
    ok: true,
    chatId: target.chatId,
    threadType: target.threadType,
    title,
    emoji,
    startsInMinutes,
    repeat,
  };
}

/** Validate a `POST /pin` body. */
export function validatePinBody(body) {
  const target = validateThreadTarget(body);
  if (!target.ok) {
    return target;
  }
  if (typeof body.pinned !== 'boolean') {
    return { ok: false, error: 'invalid_request' };
  }
  return {
    ok: true,
    chatId: target.chatId,
    threadType: target.threadType,
    pinned: body.pinned,
  };
}

/** Validate a `POST /mute` body. */
export function validateMuteBody(body) {
  const target = validateThreadTarget(body);
  if (!target.ok) {
    return target;
  }
  const action = body.action === undefined || body.action === null ? 'mute' : body.action;
  if (!MUTE_ACTIONS.includes(action)) {
    return { ok: false, error: 'invalid_request' };
  }
  const duration =
    body.duration === undefined || body.duration === null
      ? 'forever'
      : body.duration;
  if (!MUTE_DURATION_NAMES.includes(duration)) {
    return { ok: false, error: 'invalid_request' };
  }
  return {
    ok: true,
    chatId: target.chatId,
    threadType: target.threadType,
    action,
    duration,
  };
}

/**
 * Validate a `GET`/`POST /labels` body.
 *
 * Listing needs nothing; adding or removing one conversation needs the target
 * thread and the numeric label id.
 */
export function validateLabelsBody(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return { ok: false, error: 'invalid_request' };
  }
  const action = body.action === undefined || body.action === null ? 'list' : body.action;
  if (!LABEL_ACTIONS.includes(action)) {
    return { ok: false, error: 'invalid_request' };
  }
  if (action === 'list') {
    return { ok: true, action, chatId: '', threadType: 'user', labelId: '' };
  }
  const target = validateThreadTarget(body);
  if (!target.ok) {
    return target;
  }
  const labelId = numericId(
    typeof body.labelId === 'number' ? String(body.labelId) : body.labelId,
  );
  if (!labelId) {
    return { ok: false, error: 'invalid_request' };
  }
  return {
    ok: true,
    action,
    chatId: target.chatId,
    threadType: target.threadType,
    labelId,
  };
}

/** Validate a `POST /unread-mark` body. */
export function validateUnreadMarkBody(body) {
  const target = validateThreadTarget(body);
  if (!target.ok) {
    return target;
  }
  const unread = optionalBoolean(body, 'unread', true);
  if (unread === null) {
    return { ok: false, error: 'invalid_request' };
  }
  return {
    ok: true,
    chatId: target.chatId,
    threadType: target.threadType,
    unread,
  };
}

/**
 * Constant-time comparison for the bridge bearer token.
 *
 * Returns false for missing or differently sized values; equal-length values
 * are compared without an early exit.
 */
export function constantTimeEqual(expected, provided) {
  if (typeof expected !== 'string' || typeof provided !== 'string') {
    return false;
  }
  const left = Buffer.from(expected, 'utf8');
  const right = Buffer.from(provided, 'utf8');
  if (!left.length || left.length !== right.length) {
    return false;
  }
  try {
    return timingSafeEqual(left, right);
  } catch {
    return false;
  }
}

/** Extract the token from an `Authorization: Bearer <token>` header. */
export function parseBearerToken(header) {
  if (typeof header !== 'string') {
    return '';
  }
  const match = header.trim().match(/^Bearer\s+(\S+)$/i);
  return match ? match[1] : '';
}

/**
 * Token bucket shared by one endpoint class of one bridge process.
 *
 * @param {{capacity: number, perMinute: number, now?: () => number}} options
 */
export function createTokenBucket({
  capacity,
  perMinute,
  now = Date.now,
}) {
  const limit = Math.max(1, Math.floor(Number(capacity) || 1));
  const refillPerMs = Math.max(0, Number(perMinute) || 0) / 60_000;
  let tokens = limit;
  let updatedAt = now();

  function refill() {
    const current = now();
    const elapsed = Math.max(0, Number(current) - updatedAt);
    tokens = Math.min(limit, tokens + elapsed * refillPerMs);
    updatedAt = current;
    return tokens;
  }

  return {
    capacity: limit,
    take(count = 1) {
      const wanted = Math.max(1, Math.floor(Number(count) || 1));
      refill();
      if (tokens + 1e-9 < wanted) {
        return false;
      }
      tokens -= wanted;
      return true;
    },
    available() {
      return refill();
    },
  };
}

/**
 * Small TTL cache used for the friends and groups listings.
 */
export function createTtlCache({ ttlMs, now = Date.now, maxEntries = 16 } = {}) {
  const ttl = Math.max(0, Number(ttlMs) || 0);
  const limit = Math.max(1, Math.floor(Number(maxEntries) || 1));
  const entries = new Map();

  function read(key) {
    const entry = entries.get(key);
    if (!entry) {
      return undefined;
    }
    if (now() - entry.storedAt >= ttl) {
      entries.delete(key);
      return undefined;
    }
    return entry.value;
  }

  return {
    get: read,
    has(key) {
      return read(key) !== undefined;
    },
    set(key, value) {
      entries.delete(key);
      entries.set(key, { value, storedAt: now() });
      while (entries.size > limit) {
        entries.delete(entries.keys().next().value);
      }
    },
    delete(key) {
      return entries.delete(key);
    },
    clear() {
      entries.clear();
    },
    get size() {
      return entries.size;
    },
  };
}

/**
 * Split text into ordered chunks of at most `maxLength` characters.
 *
 * Cuts at the last newline (preferred) or space inside the window so a reply
 * does not break mid-word, and falls back to a hard cut for unbroken text.
 */
export function chunkText(text, maxLength = MAX_CHUNK_LENGTH) {
  const value = typeof text === 'string' ? text : '';
  if (!value) {
    return [];
  }
  const limit = Math.max(1, Math.floor(Number(maxLength) || MAX_CHUNK_LENGTH));
  if (value.length <= limit) {
    return [value];
  }
  const chunks = [];
  let start = 0;
  while (start < value.length) {
    let end = Math.min(start + limit, value.length);
    if (end < value.length) {
      const window = value.slice(start, end);
      let cut = window.lastIndexOf('\n');
      if (cut <= 0) {
        cut = window.lastIndexOf(' ');
      }
      if (cut > 0) {
        end = start + cut + 1;
      }
    }
    chunks.push(value.slice(start, end));
    start = end;
  }
  return chunks;
}

/** Split a list into ordered batches, used for `getGroupInfo`. */
export function chunkList(items, size = GROUP_INFO_BATCH_SIZE) {
  if (!Array.isArray(items)) {
    return [];
  }
  const limit = Math.max(1, Math.floor(Number(size) || GROUP_INFO_BATCH_SIZE));
  const batches = [];
  for (let index = 0; index < items.length; index += limit) {
    batches.push(items.slice(index, index + limit));
  }
  return batches;
}

/**
 * Extract the displayable text from a Zalo message content value.
 *
 * Text messages carry a string; attachments and link previews carry an object
 * with a title and description.
 */
export function messageText(content) {
  if (typeof content === 'string') {
    return content;
  }
  if (content && typeof content === 'object' && !Array.isArray(content)) {
    if (typeof content.title === 'string' && content.title.trim()) {
      return content.title;
    }
    if (typeof content.description === 'string' && content.description.trim()) {
      return content.description;
    }
  }
  return '';
}

function sizeBytesFromParams(params) {
  if (!params) {
    return null;
  }
  let parsed = params;
  if (typeof params === 'string') {
    try {
      parsed = JSON.parse(params);
    } catch {
      return null;
    }
  }
  if (!parsed || typeof parsed !== 'object') {
    return null;
  }
  for (const key of ['fileSize', 'size', 'totalSize', 'fsize']) {
    const candidate = parsed[key];
    if (candidate === undefined || candidate === null || candidate === '') {
      continue;
    }
    const value = Number(candidate);
    if (Number.isFinite(value) && value >= 0) {
      return value;
    }
  }
  return null;
}

/**
 * Describe the attachment of a message without exposing its URL.
 *
 * @returns {Array<{kind: string, name: string, sizeBytes: number | null}>}
 */
export function mediaFromContent(content) {
  if (!content || typeof content !== 'object' || Array.isArray(content)) {
    return [];
  }
  const type = typeof content.type === 'string' ? content.type : '';
  const kind = MEDIA_KINDS[type];
  if (!kind) {
    return [];
  }
  const name =
    typeof content.title === 'string' && content.title.trim()
      ? content.title
      : typeof content.description === 'string'
        ? content.description
        : '';
  return [{ kind, name, sizeBytes: sizeBytesFromParams(content.params) }];
}

/**
 * True when the inbound group mentions this account by id.
 * An @all mention (uid -1, type 1) does not count, so group-wide
 * announcements in large groups do not trigger the agent.
 */
export function mentionsSelf(mentions, ownId) {
  const me = normalizeMessageId(ownId);
  if (!me || !Array.isArray(mentions)) {
    return false;
  }
  return mentions.some((mention) => {
    if (!mention || typeof mention !== 'object' || mention.type === 1) {
      return false;
    }
    return normalizeMessageId(mention.uid) === me;
  });
}

/** True when the inbound message quotes one of this account's messages. */
export function quotesSelf(quote, ownId) {
  const me = normalizeMessageId(ownId);
  if (!me || !quote || typeof quote !== 'object') {
    return false;
  }
  return normalizeMessageId(quote.ownerId) === me;
}

/**
 * Copy the fields zca-js needs to send delivered/seen receipts.
 *
 * The receipt payload must mirror the received message exactly; only identity
 * and timing fields are kept, never content. zca-js accepts these values as
 * strings or numbers and coerces them itself.
 *
 * @param {object} data inbound `message.data`
 * @returns {object | null} receipt fields, or null without a message id
 */
export function receiptFields(data) {
  if (!data || typeof data !== 'object' || Array.isArray(data)) {
    return null;
  }
  const msgId = data.msgId ?? data.realMsgId;
  if (msgId === undefined || msgId === null || msgId === '') {
    return null;
  }
  return {
    msgId,
    cliMsgId: data.cliMsgId,
    uidFrom: data.uidFrom,
    idTo: data.idTo,
    msgType: data.msgType,
    st: data.st,
    at: data.at,
    cmd: data.cmd,
    ts: data.ts,
  };
}

function normalizeMediaItem(item) {
  if (!item || typeof item !== 'object') {
    return null;
  }
  const kind = ['image', 'file', 'video', 'other'].includes(item.kind)
    ? item.kind
    : 'other';
  const rawSize = Number(item.sizeBytes);
  return {
    kind,
    name: String(item.name || ''),
    sizeBytes: Number.isFinite(rawSize) && rawSize >= 0 ? rawSize : null,
  };
}

/**
 * Build one inbound queue item with the fields the Runtime adapter expects.
 */
export function inboundQueueItem({
  messageId,
  threadId,
  senderId,
  senderName,
  text,
  timestamp,
  threadType = 'user',
  groupName = '',
  mentionsSelf: mentioned = false,
  quotesSelf: quoted = false,
  media = [],
}) {
  const parsed = Number(timestamp);
  return {
    messageId: normalizeMessageId(messageId),
    threadId: normalizeMessageId(threadId),
    senderId: normalizeMessageId(senderId),
    senderName: String(senderName || ''),
    text: String(text || ''),
    timestamp: Number.isFinite(parsed) && parsed > 0 ? parsed : Date.now(),
    threadType: threadType === 'group' ? 'group' : 'user',
    groupName: String(groupName || ''),
    mentionsSelf: Boolean(mentioned),
    quotesSelf: Boolean(quoted),
    media: Array.isArray(media) ? media.map(normalizeMediaItem).filter(Boolean) : [],
  };
}

const RATE_LIMIT_HINTS = Object.freeze([
  'rate limit',
  'rate_limit',
  'too many',
  '429',
  'abnormal',
  'spam',
]);

/**
 * True when an upstream error looks like Zalo throttling the account.
 *
 * Only the message and code are inspected, never the request payload.
 */
export function isRateLimitError(error) {
  if (!error) {
    return false;
  }
  const message = typeof error === 'string' ? error : String(error.message || '');
  const code = typeof error === 'string' ? '' : String(error.code ?? '');
  const haystack = `${message} ${code}`.toLowerCase();
  return RATE_LIMIT_HINTS.some((hint) => haystack.includes(hint));
}

function rateLimitedError() {
  const error = new Error('rate_limited');
  error.code = 'rate_limited';
  return error;
}

/**
 * Shared gate for every upstream read-info call.
 *
 * Reads are serialized through one promise queue with at least
 * `minIntervalMs` between upstream calls so a burst of bridge requests cannot
 * look like abuse to Zalo. Each key holds a TTL cache; a throttling error
 * backs off exponentially and serves the stale value when one exists.
 *
 * @param {{minIntervalMs?: number, baseBackoffMs?: number,
 *          maxBackoffMs?: number, now?: () => number,
 *          sleep?: (ms: number) => Promise<void>}} options
 */
export function createInfoGate({
  minIntervalMs = INFO_MIN_INTERVAL_MS,
  baseBackoffMs = INFO_BASE_BACKOFF_MS,
  maxBackoffMs = INFO_MAX_BACKOFF_MS,
  now = Date.now,
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
} = {}) {
  const gap = Math.max(0, Number(minIntervalMs) || 0);
  const base = Math.max(0, Number(baseBackoffMs) || 0);
  const cap = Math.max(base, Number(maxBackoffMs) || base);
  const wait = typeof sleep === 'function' ? sleep : () => Promise.resolve();

  const entries = new Map();
  let backoffMs = 0;
  let backoffUntil = 0;
  let lastCallAt = null;
  let tail = Promise.resolve();

  function readFresh(key) {
    const entry = entries.get(key);
    if (!entry || now() >= entry.expiresAt) {
      return undefined;
    }
    return entry;
  }

  // Stale values are still readable during a backoff window.
  function readStale(key) {
    return entries.get(key);
  }

  function enqueue(task) {
    // Both handlers keep the chain alive when a task rejects.
    const result = tail.then(task, task);
    tail = result.then(
      () => undefined,
      () => undefined,
    );
    return result;
  }

  async function fetchUpstream(key, fetcher, ttlMs) {
    if (now() < backoffUntil) {
      const stale = readStale(key);
      if (stale) {
        return stale.value;
      }
      throw rateLimitedError();
    }
    if (lastCallAt !== null) {
      const remaining = gap - (now() - lastCallAt);
      if (remaining > 0) {
        await wait(remaining);
      }
    }
    lastCallAt = now();
    try {
      const value = await fetcher();
      entries.delete(key);
      entries.set(key, {
        value,
        expiresAt: now() + Math.max(0, Number(ttlMs) || 0),
      });
      backoffMs = 0;
      backoffUntil = 0;
      return value;
    } catch (error) {
      if (!isRateLimitError(error)) {
        throw error;
      }
      backoffMs = backoffMs ? Math.min(backoffMs * 2, cap) : base;
      backoffUntil = now() + backoffMs;
      const stale = readStale(key);
      if (stale) {
        return stale.value;
      }
      throw rateLimitedError();
    }
  }

  return {
    /**
     * Read one key through the gate.
     *
     * @param {string} key cache key
     * @param {() => Promise<*>} fetcher upstream call
     * @param {{ttlMs?: number, force?: boolean}} options
     */
    run(key, fetcher, { ttlMs = 0, force = false } = {}) {
      const cacheKey = String(key ?? '');
      if (!force) {
        const cached = readFresh(cacheKey);
        if (cached) {
          return Promise.resolve(cached.value);
        }
      }
      return enqueue(async () => {
        // A queued duplicate may have filled the cache while it waited.
        if (!force) {
          const cached = readFresh(cacheKey);
          if (cached) {
            return cached.value;
          }
        }
        return fetchUpstream(cacheKey, fetcher, ttlMs);
      });
    },
  };
}

/** Stable cache key for one `getGroupInfo` batch: sorted ids, joined. */
export function groupInfoCacheKey(ids) {
  const list = Array.isArray(ids) ? ids : [ids];
  return `groupinfo:${list
    .map((id) => normalizeMessageId(id))
    .filter(Boolean)
    .sort()
    .join(',')}`;
}

/** Store one group name in the bounded map, dropping the oldest entry. */
export function rememberGroupName(names, groupId, name) {
  if (!(names instanceof Map)) {
    return names;
  }
  const id = normalizeMessageId(groupId);
  const value = String(name || '');
  if (!id || !value) {
    return names;
  }
  names.delete(id);
  names.set(id, value);
  while (names.size > MAX_GROUP_NAMES) {
    names.delete(names.keys().next().value);
  }
  return names;
}

/**
 * Fill an empty `groupName` on queued group messages from the current map.
 * Messages without a known name stay unchanged.
 */
export function fillGroupNames(messages, names) {
  if (!Array.isArray(messages) || !(names instanceof Map)) {
    return messages;
  }
  for (const message of messages) {
    if (!message || typeof message !== 'object' || message.threadType !== 'group') {
      continue;
    }
    if (message.groupName) {
      continue;
    }
    const name = names.get(normalizeMessageId(message.threadId));
    if (name) {
      message.groupName = name;
    }
  }
  return messages;
}

/**
 * Extract numeric conversation ids from a pin/archive listing.
 *
 * zca-js returns raw ids or small objects depending on the account version, so
 * both shapes are accepted and anything else is skipped.
 */
export function conversationIds(values) {
  const ids = [];
  for (const value of Array.isArray(values) ? values : []) {
    const raw =
      typeof value === 'string' || typeof value === 'number'
        ? value
        : value && typeof value === 'object'
          ? value.id ?? value.conversationId ?? value.threadId ?? value.groupId ?? value.userId
          : '';
    const id = normalizeMessageId(raw);
    if (THREAD_ID_PATTERN.test(id) && !ids.includes(id)) {
      ids.push(id);
    }
  }
  return ids;
}

/**
 * Build the conversation list from the friend list, group list, pinned and
 * archived ids. Pinned entries come first; the rest keeps source order.
 */
export function conversationEntries({
  friends = [],
  groups = [],
  pinned = [],
  archived = [],
} = {}) {
  const pinnedIds = new Set(conversationIds(pinned));
  const archivedIds = new Set(conversationIds(archived));
  const entries = [];
  const seen = new Set();
  for (const group of Array.isArray(groups) ? groups : []) {
    const id = normalizeMessageId(group?.groupId);
    if (!THREAD_ID_PATTERN.test(id) || seen.has(id)) {
      continue;
    }
    seen.add(id);
    entries.push({
      kind: 'group',
      id,
      name: String(group?.name || ''),
      member_count: Number(group?.memberCount) || 0,
      pinned: pinnedIds.has(id),
      archived: archivedIds.has(id),
    });
  }
  for (const friend of Array.isArray(friends) ? friends : []) {
    const id = normalizeMessageId(friend?.userId);
    if (!THREAD_ID_PATTERN.test(id) || seen.has(id)) {
      continue;
    }
    seen.add(id);
    entries.push({
      kind: 'dm',
      id,
      name: String(friend?.displayName || friend?.zaloName || ''),
      member_count: 0,
      pinned: pinnedIds.has(id),
      archived: archivedIds.has(id),
    });
  }
  entries.sort((left, right) => Number(right.pinned) - Number(left.pinned));
  return entries;
}

/**
 * Shape `getGroupChatHistory` messages for the Runtime tool.
 *
 * @param {Array<{data: object, isSelf?: boolean}>} messages
 */
export function groupHistoryRecords(messages, { textLimit = MAX_CHUNK_LENGTH } = {}) {
  const records = [];
  for (const message of Array.isArray(messages) ? messages : []) {
    const data = message?.data || {};
    const messageId = normalizeMessageId(data.msgId ?? data.realMsgId);
    if (!messageId) {
      continue;
    }
    const media = mediaFromContent(data.content);
    const timestamp = Number(data.ts);
    records.push({
      message_id: messageId,
      sender_id: normalizeMessageId(data.uidFrom),
      sender_name: String(data.dName || ''),
      text: messageText(data.content).slice(0, textLimit),
      ts: Number.isFinite(timestamp) && timestamp > 0 ? timestamp : 0,
      type: media.length ? media[0].kind : 'text',
      direction: message?.isSelf ? 'out' : 'in',
    });
  }
  // Zalo does not guarantee message order; history reads expect oldest first.
  records.sort((left, right) => Number(left.ts || 0) - Number(right.ts || 0));
  return records;
}

/** Role of one member inside a `GroupInfo` payload. */
export function groupMemberRole(group, memberId) {
  const id = normalizeMessageId(memberId);
  if (!id) {
    return 'member';
  }
  if (normalizeMessageId(group?.creatorId) === id) {
    return 'creator';
  }
  if (Array.isArray(group?.adminIds) && group.adminIds.some((value) => normalizeMessageId(value) === id)) {
    return 'admin';
  }
  return 'member';
}

/**
 * Shape one group and its member preview for the Runtime tool.
 *
 * @param {object} group zca-js `GroupInfo`
 * @param {Record<string, object>} profiles `getGroupMembersInfo` profiles map
 */
export function groupInfoRecord(group, profiles, { memberLimit = GROUP_INFO_BATCH_SIZE } = {}) {
  const info = group || {};
  const memberIds = Array.isArray(info.memberIds) ? info.memberIds : [];
  const limit = Math.max(0, Math.min(MAX_GROUP_MEMBER_LIMIT, Math.floor(Number(memberLimit) || 0)));
  const map = profiles && typeof profiles === 'object' ? profiles : {};
  const members = [];
  for (const memberId of memberIds) {
    if (members.length >= limit) {
      break;
    }
    const id = normalizeMessageId(memberId);
    if (!id) {
      continue;
    }
    const profile = map[id] || {};
    members.push({
      user_id: id,
      display_name: String(profile.displayName || profile.zaloName || ''),
      role: groupMemberRole(info, id),
    });
  }
  return {
    group_id: normalizeMessageId(info.groupId),
    name: String(info.name || ''),
    description: String(info.desc || ''),
    member_count: Number(info.totalMember) || memberIds.length,
    created_time: Number(info.createdTime) || 0,
    creator_id: normalizeMessageId(info.creatorId),
    members,
  };
}

/** Shape `getListReminder` entries (group reminders). */
export function reminderRecords(items) {
  const records = [];
  for (const item of Array.isArray(items) ? items : []) {
    if (!item || typeof item !== 'object') {
      continue;
    }
    records.push({
      reminder_id: normalizeMessageId(item.reminderId ?? item.id),
      title: String(item.params?.title || ''),
      creator_id: normalizeMessageId(item.creatorId),
      start_time: Number(item.startTime) || 0,
      repeat: Number(item.repeat) || 0,
    });
  }
  return records;
}

/** Shape one `createNote` payload. */
export function noteRecord(note) {
  const value = note || {};
  return {
    note_id: normalizeMessageId(value.id),
    title: String(value.params?.title || ''),
    creator_id: normalizeMessageId(value.creatorId),
    created_time: Number(value.createTime) || 0,
    updated_time: Number(value.editTime) || 0,
  };
}

/** Shape `getListBoard` items into notes only. */
export function noteRecords(items) {
  const records = [];
  for (const item of Array.isArray(items) ? items : []) {
    if (item?.boardType !== undefined && Number(item.boardType) !== NOTE_BOARD_TYPE) {
      continue;
    }
    const data = item?.data;
    if (!data || typeof data !== 'object') {
      continue;
    }
    records.push({
      note_id: normalizeMessageId(data.id),
      title: String(data.params?.title || ''),
      creator_id: normalizeMessageId(data.creatorId),
      created_time: Number(data.createTime) || 0,
      updated_time: Number(data.editTime) || 0,
    });
  }
  return records;
}

/**
 * Shape `getLabels`/`updateLabels` label data for the Runtime tool.
 *
 * Only the display fields are exposed; conversation ids stay in the bridge.
 */
export function labelEntries(labelData) {
  const labels = [];
  for (const label of Array.isArray(labelData) ? labelData : []) {
    if (!label || typeof label !== 'object') {
      continue;
    }
    const id = Number(label.id);
    if (!Number.isInteger(id) || id <= 0) {
      continue;
    }
    labels.push({
      label_id: String(id),
      name: String(label.text || ''),
      conversation_count: Array.isArray(label.conversations)
        ? label.conversations.length
        : 0,
    });
  }
  return labels;
}

/**
 * Conversation token used inside one Zalo label.
 *
 * Zalo labels address conversations with the same `u<id>`/`g<id>` form the
 * pin API uses; entries that predate this bridge are preserved untouched.
 */
export function conversationLabelToken(chatId, threadType) {
  const id = normalizeMessageId(chatId);
  return `${threadType === 'group' ? 'g' : 'u'}${id}`;
}

function labelConversationMatches(value, chatId) {
  const text = String(value || '').trim();
  const id = normalizeMessageId(chatId);
  if (!id || !text) {
    return false;
  }
  return (
    text === id ||
    text === `u${id}` ||
    text === `g${id}`
  );
}

/**
 * Add or remove one conversation from one label.
 *
 * Returns a new label list; the caller sends it back through `updateLabels`
 * with the version it read, so no other label is changed.
 *
 * @returns {{ok: true, labelData: Array, label: object} |
 *           {ok: false, error: string}}
 */
export function applyLabelChange(labelData, { labelId, chatId, threadType, add }) {
  if (!Array.isArray(labelData)) {
    return { ok: false, error: 'invalid_response' };
  }
  const id = Number(labelId);
  const token = conversationLabelToken(chatId, threadType);
  const updated = [];
  let target = null;
  for (const label of labelData) {
    if (!label || typeof label !== 'object') {
      return { ok: false, error: 'invalid_response' };
    }
    const conversations = Array.isArray(label.conversations)
      ? label.conversations.map((value) => String(value))
      : [];
    if (Number(label.id) !== id) {
      updated.push(label);
      continue;
    }
    if (conversations.length > MAX_LABEL_CONVERSATIONS) {
      return { ok: false, error: 'invalid_response' };
    }
    let next = conversations.filter(
      (value) => !labelConversationMatches(value, chatId),
    );
    if (add) {
      if (next.length >= MAX_LABEL_CONVERSATIONS) {
        return { ok: false, error: 'invalid_response' };
      }
      next = [...next, token];
    }
    target = { ...label, conversations: next };
    updated.push(target);
  }
  if (!target) {
    return { ok: false, error: 'not_found' };
  }
  return {
    ok: true,
    labelData: updated,
    label: {
      label_id: String(id),
      name: String(target.text || ''),
      conversation_count: target.conversations.length,
    },
  };
}

/** Shape one `getPollDetail` payload. */
export function pollRecord(poll) {
  const value = poll || {};
  const options = [];
  for (const option of Array.isArray(value.options) ? value.options : []) {
    options.push({
      option_id: Number(option?.option_id) || 0,
      content: String(option?.content || ''),
      votes: Number(option?.votes) || 0,
      voted: Boolean(option?.voted),
    });
  }
  return {
    poll_id: normalizeMessageId(value.poll_id),
    question: String(value.question || ''),
    options,
    closed: Boolean(value.closed),
    anonymous: Boolean(value.is_anonymous),
    multi_choice: Boolean(value.allow_multi_choices),
    vote_count: Number(value.num_vote) || 0,
    created_time: Number(value.created_time) || 0,
  };
}

/**
 * Shape `getUnreadMark` into unread conversation records.
 *
 * @param {{data?: {convsUser?: Array, convsGroup?: Array}}} mark
 */
export function unreadRecords(mark) {
  const data = mark?.data && typeof mark.data === 'object' ? mark.data : {};
  const shape = (items) => {
    const records = [];
    for (const item of Array.isArray(items) ? items : []) {
      const threadId = normalizeMessageId(item?.id);
      if (!THREAD_ID_PATTERN.test(threadId)) {
        continue;
      }
      records.push({
        thread_id: threadId,
        last_message_id: normalizeMessageId(item?.cliMsgId),
        ts: Number(item?.ts) || 0,
      });
    }
    return records;
  };
  return {
    direct: shape(data.convsUser),
    groups: shape(data.convsGroup),
  };
}

/** Shape one user profile from `getUserInfo`, `findUser`, or `findUserByUsername`. */
export function userInfoRecord(profile) {
  const value = profile || {};
  return {
    user_id: normalizeMessageId(value.userId ?? value.uid),
    display_name: String(value.displayName ?? value.display_name ?? ''),
    zalo_name: String(value.zaloName ?? value.zalo_name ?? ''),
    avatar: String(value.avatar || ''),
    is_friend: Number(value.isFr) === 1 || value.isFr === true,
    gender: Number(value.gender) || 0,
    birthday: String(value.sdob || ''),
  };
}

// zca-js TextStyle values (dist/apis/sendMessage.js:
// Bold "b", Italic "i", StrikeThrough "s").
const ST_BOLD = 'b';
const ST_ITALIC = 'i';
const ST_STRIKE = 's';

const WORD_CHAR = /[\p{L}\p{N}]/u;

const INLINE_PAIRS = Object.freeze([
  { marker: '**', st: ST_BOLD, wordSafe: false },
  { marker: '__', st: ST_BOLD, wordSafe: true },
  { marker: '~~', st: ST_STRIKE, wordSafe: false },
  { marker: '*', st: ST_ITALIC, wordSafe: false },
  { marker: '_', st: ST_ITALIC, wordSafe: true },
]);

function canOpenMarker(source, index, marker, wordSafe) {
  const next = source[index + marker.length] || '';
  if (!next || /\s/.test(next)) {
    return false;
  }
  if (wordSafe) {
    const before = index > 0 ? source[index - 1] : '';
    if (before && WORD_CHAR.test(before)) {
      return false;
    }
  }
  return true;
}

function findClosingMarker(source, from, marker, wordSafe) {
  const step = marker.length;
  for (let index = from; index + step <= source.length; index += 1) {
    if (!source.startsWith(marker, index)) {
      continue;
    }
    const before = index > 0 ? source[index - 1] : '';
    const after = source[index + step] || '';
    if (step === 1 && (before === marker || after === marker)) {
      continue;
    }
    if (step === 2 && before === marker[0]) {
      continue;
    }
    if (!before || /\s/.test(before)) {
      continue;
    }
    if (wordSafe && after && WORD_CHAR.test(after)) {
      continue;
    }
    return index;
  }
  return -1;
}

/** Append a parsed fragment and rebase its styles onto the outer text. */
function appendStyled(target, fragment) {
  const shift = target.msg.length;
  for (const style of fragment.styles) {
    target.styles.push({ start: style.start + shift, len: style.len, st: style.st });
  }
  target.msg += fragment.msg;
}

function tryInlinePair(source, index, depth) {
  for (const pair of INLINE_PAIRS) {
    if (!source.startsWith(pair.marker, index)) {
      continue;
    }
    if (!canOpenMarker(source, index, pair.marker, pair.wordSafe)) {
      continue;
    }
    const contentStart = index + pair.marker.length;
    const close = findClosingMarker(source, contentStart + 1, pair.marker, pair.wordSafe);
    if (close < 0) {
      continue;
    }
    const inner = parseInline(source.slice(contentStart, close), depth + 1);
    if (!inner.msg) {
      continue;
    }
    const styles = inner.styles.slice();
    styles.push({ start: 0, len: inner.msg.length, st: pair.st });
    return { msg: inner.msg, styles, end: close + pair.marker.length };
  }
  return null;
}

function parseInline(source, depth = 0) {
  const result = { msg: '', styles: [] };
  if (depth > 8) {
    result.msg = source;
    return result;
  }
  let index = 0;
  while (index < source.length) {
    if (source[index] === '`') {
      const close = source.indexOf('`', index + 1);
      if (close > index + 1) {
        result.msg += source.slice(index + 1, close);
        index = close + 1;
        continue;
      }
      result.msg += '`';
      index += 1;
      continue;
    }
    if (source[index] === '[') {
      const link = /^\[([^\]\n]+)\]\(([^()\s]+)\)/.exec(source.slice(index));
      if (link) {
        const label = parseInline(link[1], depth + 1);
        appendStyled(result, label);
        result.msg += ` (${link[2]})`;
        index += link[0].length;
        continue;
      }
    }
    const paired = tryInlinePair(source, index, depth);
    if (paired) {
      appendStyled(result, paired);
      index = paired.end;
      continue;
    }
    result.msg += source[index];
    index += 1;
  }
  return result;
}

function convertMarkdownLine(line) {
  const heading = /^(#{1,6})[ \t]+(.+)$/.exec(line);
  if (heading) {
    const content = heading[2].replace(/[ \t]+#+[ \t]*$/, '');
    const parsed = parseInline(content);
    if (parsed.msg) {
      parsed.styles.push({ start: 0, len: parsed.msg.length, st: ST_BOLD });
    }
    return parsed;
  }
  const bullet = /^(\s*)[-*+][ \t]+(.*)$/.exec(line);
  if (bullet) {
    const shift = bullet[1].length + 2;
    const parsed = parseInline(bullet[2]);
    return {
      msg: `${bullet[1]}• ${parsed.msg}`,
      styles: parsed.styles.map((style) => ({
        start: style.start + shift,
        len: style.len,
        st: style.st,
      })),
    };
  }
  return parseInline(line);
}

/**
 * Convert a conservative Markdown subset into Zalo rich text.
 *
 * Returns `{ msg, styles }` where `styles` uses zca-js `Style` entries
 * (`{ start, len, st }`, UTF-16 offsets into `msg`). Never throws: input that
 * cannot be parsed is returned unchanged.
 */
export function markdownToZalo(text) {
  const source = typeof text === 'string' ? text : String(text ?? '');
  try {
    const parts = [];
    let fenced = false;
    for (const line of source.split('\n')) {
      if (/^\s*```/.test(line)) {
        fenced = !fenced;
        continue;
      }
      parts.push(fenced ? { msg: line, styles: [] } : convertMarkdownLine(line));
    }
    let msg = '';
    const styles = [];
    parts.forEach((part, index) => {
      if (index > 0) {
        msg += '\n';
      }
      const base = msg.length;
      msg += part.msg;
      for (const style of part.styles) {
        styles.push({ start: style.start + base, len: style.len, st: style.st });
      }
    });
    return { msg, styles };
  } catch {
    return { msg: source, styles: [] };
  }
}

/**
 * Split a styled message into sendable chunks.
 *
 * The clean text is chunked with `chunkText`; each style span is clipped to
 * the chunk and rebased so a span crossing a boundary appears in both chunks.
 */
export function splitStyledChunks(msg, styles, size = MAX_CHUNK_LENGTH) {
  const text = typeof msg === 'string' ? msg : '';
  const chunks = chunkText(text, size);
  if (chunks.length === 0) {
    return [];
  }
  const normalized = Array.isArray(styles) ? styles : [];
  let offset = 0;
  return chunks.map((chunk) => {
    const start = offset;
    const end = offset + chunk.length;
    offset = end;
    const chunkStyles = [];
    for (const style of normalized) {
      if (!style || typeof style !== 'object') {
        continue;
      }
      const styleStart = Math.max(0, Math.floor(Number(style.start) || 0));
      const styleLen = Math.max(0, Math.floor(Number(style.len) || 0));
      if (!styleLen) {
        continue;
      }
      const overlapStart = Math.max(styleStart, start);
      const overlapEnd = Math.min(styleStart + styleLen, end);
      if (overlapEnd <= overlapStart) {
        continue;
      }
      chunkStyles.push({
        start: overlapStart - start,
        len: overlapEnd - overlapStart,
        st: style.st,
      });
    }
    return { msg: chunk, styles: chunkStyles };
  });
}
