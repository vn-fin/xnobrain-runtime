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

export const READ_RATE_LIMIT_PER_MINUTE = 30;
export const SEND_RATE_LIMIT_PER_MINUTE = 20;
export const LIST_CACHE_TTL_MS = 5 * 60_000;
export const GROUP_INFO_BATCH_SIZE = 50;

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
 * Build one inbound-history record.
 *
 * The bridge appends these as JSON lines so the Runtime tool can resolve a
 * human name to a Zalo thread without reading the live queue.
 */
export function historyEntry({
  messageId,
  threadId,
  senderId,
  senderName,
  text,
  timestamp,
  recovered = false,
}) {
  const parsed = Number(timestamp);
  return {
    ts: Number.isFinite(parsed) && parsed > 0 ? parsed : Date.now(),
    messageId: normalizeMessageId(messageId),
    threadId: normalizeMessageId(threadId),
    senderId: normalizeMessageId(senderId),
    senderName: String(senderName || ''),
    text: String(text || ''),
    recovered: Boolean(recovered),
  };
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

/** Validate a `POST /send` body, including `threadType`. */
export function validateSendMessageBody(body) {
  const target = validateThreadTarget(body);
  if (!target.ok) {
    return target;
  }
  if (!isValidMessageText(body.message)) {
    return { ok: false, error: 'invalid_request' };
  }
  return {
    ok: true,
    chatId: target.chatId,
    threadType: target.threadType,
    message: body.message,
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
  const fallback = `image.${extension}`;
  const raw = typeof name === 'string' ? name.split(/[\\/]/).pop() : '';
  const cleaned = String(raw || '')
    .replace(/[\u0000-\u001f\u007f]/g, '')
    .trim()
    .slice(0, 100);
  if (!cleaned || cleaned === '.' || cleaned === '..') {
    return fallback;
  }
  const dot = cleaned.lastIndexOf('.');
  const base = (dot > 0 ? cleaned.slice(0, dot) : cleaned).trim();
  if (!base || base === '.' || base === '..') {
    return fallback;
  }
  return `${base}.${extension}`;
}

/** Validate a `POST /send-media` body and decode the image bytes. */
export function validateSendMediaBody(body) {
  const target = validateThreadTarget(body);
  if (!target.ok) {
    return target;
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
  const detected = detectImageType(buffer);
  if (!detected) {
    return { ok: false, error: 'invalid_request' };
  }
  return {
    ok: true,
    chatId: target.chatId,
    threadType: target.threadType,
    fileName: safeImageFileName(body.fileName, detected.extension),
    caption: typeof body.caption === 'string' ? body.caption : '',
    mimeType: detected.mime,
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
