/**
 * Pure helpers shared by the Zalo bridge and its tests.
 *
 * Keeping these free of zca-js imports lets the test suite exercise the queue,
 * cursor and request-validation rules without a live Zalo session.
 */

export const MAX_QUEUE_SIZE = 100;

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
 * @returns {{ok: true, chatId: string, message: string} |
 *           {ok: false, error: string}}
 */
export function validateSendBody(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return { ok: false, error: 'Request body must be a JSON object' };
  }
  const chatId = body.chatId;
  const message = body.message;
  if (typeof chatId !== 'string' || !chatId.trim()) {
    return { ok: false, error: 'chatId must be a non-empty string' };
  }
  if (typeof message !== 'string' || !message.trim()) {
    return { ok: false, error: 'message must be a non-empty string' };
  }
  return { ok: true, chatId: chatId.trim(), message };
}
