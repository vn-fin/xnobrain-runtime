#!/usr/bin/env node
/**
 * Zalo personal-account bridge for the XNOBrain/Hermes runtime.
 *
 * Standalone Node.js process that logs into Zalo through zca-js and exposes a
 * loopback-only HTTP API for the Python platform adapter.
 *
 * Endpoints:
 *   GET  /health      - open; {status, scriptHash, ownId}
 *   POST /login       - start one on-demand QR login
 *   GET  /messages    - drain queued inbound messages
 *   GET  /me          - own profile
 *   GET  /friends     - friend list, cached 5 minutes
 *   GET  /groups      - group list, cached 5 minutes
 *   POST /send        - chunked text message
 *   POST /send-media  - image attachment
 *   POST /send-link   - link preview
 *   POST /react       - reaction on a recent inbound message
 *   POST /typing      - typing indicator (best effort)
 *   POST /seen        - delivered/seen receipts (best effort)
 *
 * Every endpoint except GET /health requires `Authorization: Bearer <token>`,
 * where the token is the random value written to `<session>/bridge.token` at
 * every start (mode 0600).
 *
 * Usage:
 *   node bridge.js --port 3110 --session <HERMES_HOME>/zalo
 *
 * Inbound group messages are forwarded like direct messages; history.jsonl
 * still stores direct messages only.
 */

import express from 'express';
import QRCode from 'qrcode';
import path from 'path';
import { homedir, tmpdir } from 'os';
import { createHash, randomBytes } from 'crypto';
import { fileURLToPath } from 'url';
import {
  appendFileSync,
  chmodSync,
  closeSync,
  existsSync,
  fsyncSync,
  mkdirSync,
  mkdtempSync,
  openSync,
  readFileSync,
  renameSync,
  rmSync,
  unlinkSync,
  writeFileSync,
} from 'fs';
import {
  LoginQRCallbackEventType,
  Reactions,
  ThreadType,
  Zalo,
} from 'zca-js';

import {
  GROUP_INFO_BATCH_SIZE,
  GROUP_INFO_CACHE_TTL_MS,
  LIST_CACHE_TTL_MS,
  MAX_QUEUE_SIZE,
  MAX_RELOGIN_ATTEMPTS,
  READ_RATE_LIMIT_PER_MINUTE,
  SEND_RATE_LIMIT_PER_MINUTE,
  chunkList,
  classifyCloseCode,
  constantTimeEqual,
  cookieListFromJar,
  createInfoGate,
  createTokenBucket,
  fillGroupNames,
  groupInfoCacheKey,
  historyEntry,
  inboundQueueItem,
  isLoopback,
  isNewerMessageId,
  markdownToZalo,
  mediaFromContent,
  mentionsSelf,
  messageText,
  nextQrExpiryAttempt,
  normalizeMessageId,
  parseBearerToken,
  pushToQueue,
  quotesSelf,
  receiptFields,
  reloginDelayMs,
  rememberCursor,
  rememberGroupName,
  shouldQueueMessage,
  splitStyledChunks,
  trimHistoryLines,
  validateReactBody,
  validateSeenBody,
  validateSendLinkBody,
  validateSendMediaBody,
  validateSendMessageBody,
  validateTypingBody,
} from './lib.js';

const DEFAULT_PORT = 3110;
const KEEPALIVE_INTERVAL_MS = 60_000;
const SESSION_SAVE_INTERVAL_MS = 10 * 60_000;
const MAX_SEEN_IDS = 2_000;
const MAX_REMEMBERED_MESSAGES = 500;

const args = process.argv.slice(2);

function getArg(name, fallback) {
  const index = args.indexOf(`--${name}`);
  return index !== -1 && args[index + 1] ? args[index + 1] : fallback;
}

function parsePort(raw) {
  const parsed = Number.parseInt(raw, 10);
  if (!Number.isInteger(parsed) || parsed < 1 || parsed > 65_535 || parsed === 3000) {
    return DEFAULT_PORT;
  }
  return parsed;
}

const PORT = parsePort(getArg('port', String(DEFAULT_PORT)));
const SESSION_DIR = path.resolve(
  getArg('session', path.join(homedir(), '.hermes', 'zalo')),
);
const SESSION_FILE = path.join(SESSION_DIR, 'session.json');
const CURSOR_FILE = path.join(SESSION_DIR, 'cursor.json');
const HISTORY_FILE = path.join(SESSION_DIR, 'history.jsonl');
const QR_PATH = path.join(SESSION_DIR, 'qr.png');
const TOKEN_FILE = path.join(SESSION_DIR, 'bridge.token');

// A fresh bearer token is generated for every bridge start; Runtime reads the
// file before each request and never caches it.
const BRIDGE_TOKEN = randomBytes(32).toString('hex');

const readBucket = createTokenBucket({
  capacity: READ_RATE_LIMIT_PER_MINUTE,
  perMinute: READ_RATE_LIMIT_PER_MINUTE,
});
const sendBucket = createTokenBucket({
  capacity: SEND_RATE_LIMIT_PER_MINUTE,
  perMinute: SEND_RATE_LIMIT_PER_MINUTE,
});
// One gate serializes every upstream read-info call, so Zalo never sees two
// profile/list/group-info requests close together. It also owns the TTL cache
// shared by /me, /friends, /groups and the inbound group-name lookups.
const infoGate = createInfoGate();

// Recently received inbound messages, used to resolve reaction and receipt
// targets. Bounded so a long-running bridge cannot grow without limit.
const inboundMessages = new Map();
const groupNames = new Map();
// Group ids with a name lookup currently in flight.
const groupNameLookups = new Set();

function writeBridgeToken() {
  try {
    mkdirSync(SESSION_DIR, { recursive: true, mode: 0o700 });
    writeTextAtomic(TOKEN_FILE, BRIDGE_TOKEN, 0o600);
  } catch {
    console.log('[zalo] failed to write bridge token');
  }
}

let SCRIPT_HASH = '';
try {
  SCRIPT_HASH = createHash('sha256')
    .update(readFileSync(fileURLToPath(import.meta.url)))
    .digest('hex')
    .slice(0, 16);
} catch {
  // /health simply reports an empty hash when the script cannot be read.
}

// status: "needs_login" | "connecting" | "qr" | "connected" | "closed"
// A bridge without a usable session starts at needs_login and only generates a
// QR after an explicit POST /login, so an unattended workspace cannot spin.
let connectionState = 'needs_login';
let api = null;
let ownId = '';
let shuttingDown = false;
let suppressCloseEvents = false;
let terminalClosed = false;
let terminalCloseLogged = false;
let reloginAttempt = 0;
let reloginTimer = null;
let loginInFlight = false;
let qrExpiredCount = 0;
let qrLoginAborted = false;
let keepAliveTimer = null;
let sessionSaveTimer = null;
let sessionIdentity = null;

const messageQueue = [];
const seenIds = new Set();
const seenOrder = [];
const cursors = loadJsonFile(CURSOR_FILE, {});

function loadJsonFile(filePath, fallback) {
  try {
    const parsed = JSON.parse(readFileSync(filePath, 'utf8'));
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return parsed;
    }
  } catch {
    // Missing or malformed state falls back to an empty value.
  }
  return fallback;
}

function writeJsonAtomic(filePath, value, mode = 0o600) {
  const temporary = `${filePath}.tmp-${process.pid}-${Date.now()}`;
  const payload = `${JSON.stringify(value)}\n`;
  const fd = openSync(temporary, 'w', mode);
  try {
    writeFileSync(fd, payload, 'utf8');
    fsyncSync(fd);
  } finally {
    closeSync(fd);
  }
  try {
    chmodSync(temporary, mode);
  } catch {
    // Best effort on filesystems without POSIX modes.
  }
  renameSync(temporary, filePath);
  try {
    const directory = openSync(path.dirname(filePath), 'r');
    try {
      fsyncSync(directory);
    } finally {
      closeSync(directory);
    }
  } catch {
    // Directory fsync is not supported everywhere; the rename is still atomic.
  }
}

function persistCursors() {
  try {
    writeJsonAtomic(CURSOR_FILE, cursors, 0o600);
  } catch {
    console.log('[zalo] failed to persist catch-up cursor');
  }
}

function writeTextAtomic(filePath, text, mode = 0o600) {
  const temporary = `${filePath}.tmp-${process.pid}-${Date.now()}`;
  const fd = openSync(temporary, 'w', mode);
  try {
    writeFileSync(fd, text, 'utf8');
    fsyncSync(fd);
  } finally {
    closeSync(fd);
  }
  try {
    chmodSync(temporary, mode);
  } catch {
    // Best effort on filesystems without POSIX modes.
  }
  renameSync(temporary, filePath);
}

function appendHistory(message, { recovered = false } = {}) {
  const entry = historyEntry({
    messageId: message.messageId,
    threadId: message.threadId,
    senderId: message.senderId,
    senderName: message.senderName,
    text: message.text,
    timestamp: message.timestamp,
    recovered,
  });
  try {
    appendFileSync(HISTORY_FILE, `${JSON.stringify(entry)}\n`, {
      encoding: 'utf8',
      mode: 0o600,
    });
  } catch {
    console.log('[zalo] failed to append inbound history');
  }
}

/**
 * Keep only the last 30 days of inbound history. Runs once at bridge start so
 * the append-only file cannot grow without bound across sessions.
 */
function trimHistory() {
  try {
    if (!existsSync(HISTORY_FILE)) {
      return;
    }
    const lines = readFileSync(HISTORY_FILE, 'utf8').split(/\r?\n/);
    const kept = trimHistoryLines(lines);
    const payload = kept.length ? `${kept.join('\n')}\n` : '';
    writeTextAtomic(HISTORY_FILE, payload, 0o600);
  } catch {
    console.log('[zalo] failed to trim inbound history');
  }
}

function rememberSeen(messageId) {
  seenIds.add(messageId);
  seenOrder.push(messageId);
  while (seenOrder.length > MAX_SEEN_IDS) {
    seenIds.delete(seenOrder.shift());
  }
}

function rememberInboundMessage(messageId, entry) {
  inboundMessages.delete(messageId);
  inboundMessages.set(messageId, entry);
  while (inboundMessages.size > MAX_REMEMBERED_MESSAGES) {
    inboundMessages.delete(inboundMessages.keys().next().value);
  }
}

function threadTypeName(type) {
  return type === ThreadType.Group ? 'group' : 'user';
}

function toZaloThreadType(name) {
  return name === 'group' ? ThreadType.Group : ThreadType.User;
}

function loadSession() {
  if (!existsSync(SESSION_FILE)) {
    return null;
  }
  const session = loadJsonFile(SESSION_FILE, null);
  if (!session || !session.cookie || !session.imei || !session.userAgent) {
    return null;
  }
  return session;
}

function saveSession(credentials) {
  if (!credentials?.cookie || !credentials?.imei || !credentials?.userAgent) {
    return;
  }
  try {
    writeJsonAtomic(
      SESSION_FILE,
      {
        cookie: credentials.cookie,
        imei: credentials.imei,
        userAgent: credentials.userAgent,
      },
      0o600,
    );
    console.log('[zalo] session saved');
  } catch {
    console.log('[zalo] failed to save session');
  }
}

/**
 * Learn one group's name without blocking message ingestion.
 *
 * Called only when an inbound group message has no known name. The lookup
 * goes through the shared info gate (10 minute cache), is deduplicated per
 * group id while in flight, and failures are ignored: a later message retries.
 */
function requestGroupName(groupId) {
  const id = normalizeMessageId(groupId);
  if (!id || groupNames.has(id) || groupNameLookups.has(id) || !isConnected()) {
    return;
  }
  groupNameLookups.add(id);
  const lookup = infoGate.run(
    groupInfoCacheKey([id]),
    () => api.getGroupInfo([id]),
    { ttlMs: GROUP_INFO_CACHE_TTL_MS },
  );
  void lookup
    .then((info) => {
      const group = info?.gridInfoMap?.[id];
      const name = String(group?.name || '');
      if (name) {
        rememberGroupName(groupNames, id, name);
      }
    })
    .catch(() => {
      // A transient failure only means the name stays unknown for now.
    })
    .finally(() => {
      groupNameLookups.delete(id);
    });
}

function ingestMessage(message, { requireNewer = false } = {}) {
  if (!message) {
    return false;
  }
  if (message.type !== ThreadType.User && message.type !== ThreadType.Group) {
    return false;
  }
  if (message.isSelf) {
    return false;
  }
  const data = message.data || {};
  const threadType = threadTypeName(message.type);
  const text = messageText(data.content);
  const media = mediaFromContent(data.content);
  // Keep attachment-only messages; drop messages with neither text nor media.
  if (!text.trim() && media.length === 0) {
    return false;
  }
  const threadId = normalizeMessageId(
    message.threadId || (threadType === 'user' ? data.uidFrom : ''),
  );
  const senderId = normalizeMessageId(data.uidFrom || threadId);
  const messageId = normalizeMessageId(data.msgId ?? data.realMsgId);
  if (!threadId || !senderId || !messageId) {
    return false;
  }
  if (ownId && senderId === normalizeMessageId(ownId)) {
    return false;
  }
  if (
    !shouldQueueMessage({
      cursor: cursors,
      seenIds,
      threadId,
      messageId,
      requireNewer,
    })
  ) {
    return false;
  }

  rememberSeen(messageId);
  rememberCursor(cursors, threadId, messageId);
  persistCursors();
  const senderName = String(data.dName || '');
  const timestamp = Number(data.ts) || Date.now();
  const groupName = threadType === 'group' ? groupNames.get(threadId) || '' : '';
  if (threadType === 'group' && !groupName) {
    // Fire and forget; GET /messages fills the name if this lookup finishes
    // before the queue is drained.
    requestGroupName(threadId);
  }
  const item = inboundQueueItem({
    messageId,
    threadId,
    senderId,
    senderName,
    text,
    timestamp,
    threadType,
    groupName,
    mentionsSelf:
      threadType === 'group' ? mentionsSelf(data.mentions, ownId) : false,
    quotesSelf: quotesSelf(data.quote, ownId),
    media,
  });
  rememberInboundMessage(messageId, {
    cliMsgId: normalizeMessageId(data.cliMsgId),
    threadId,
    threadType,
    receipt: receiptFields(data),
  });
  pushToQueue(messageQueue, item);
  // Group history is a later plan (P-B); keep the existing DM-only history.
  if (threadType === 'user') {
    appendHistory(
      {
        messageId,
        threadId,
        senderId,
        senderName,
        text,
        timestamp,
      },
      { recovered: Boolean(requireNewer) },
    );
  }
  return true;
}

function handleOldMessages(messages, threadType) {
  if (
    (threadType !== ThreadType.User && threadType !== ThreadType.Group) ||
    !Array.isArray(messages)
  ) {
    return;
  }
  for (const message of messages) {
    ingestMessage(message, { requireNewer: true });
  }
}

function requestCatchUp() {
  if (!api?.listener) {
    return;
  }
  let latest = '';
  for (const messageId of Object.values(cursors)) {
    if (isNewerMessageId(messageId, latest)) {
      latest = normalizeMessageId(messageId);
    }
  }
  try {
    api.listener.requestOldMessages(ThreadType.User, latest || null);
  } catch {
    console.log('[zalo] catch-up request unavailable');
  }
}

function stopKeepAlive() {
  if (keepAliveTimer) {
    clearInterval(keepAliveTimer);
    keepAliveTimer = null;
  }
}

function startKeepAlive() {
  stopKeepAlive();
  keepAliveTimer = setInterval(() => {
    if (!api || connectionState !== 'connected') {
      return;
    }
    Promise.resolve()
      .then(() => api.keepAlive())
      .catch(() => {
        console.log('[zalo] keepalive failed');
      });
  }, KEEPALIVE_INTERVAL_MS);
  if (typeof keepAliveTimer.unref === 'function') {
    keepAliveTimer.unref();
  }
}

function stopSessionSave() {
  if (sessionSaveTimer) {
    clearInterval(sessionSaveTimer);
    sessionSaveTimer = null;
  }
}

function startSessionSave() {
  stopSessionSave();
  sessionSaveTimer = setInterval(() => {
    persistSessionFromApi();
  }, SESSION_SAVE_INTERVAL_MS);
  if (typeof sessionSaveTimer.unref === 'function') {
    sessionSaveTimer.unref();
  }
}

function persistSessionFromApi() {
  if (!api || !sessionIdentity?.imei || !sessionIdentity?.userAgent) {
    return;
  }
  let cookies;
  try {
    cookies = cookieListFromJar(api.getCookie());
  } catch {
    return;
  }
  if (!cookies || cookies.length === 0) {
    return;
  }
  saveSession({
    cookie: cookies,
    imei: sessionIdentity.imei,
    userAgent: sessionIdentity.userAgent,
  });
}

/**
 * Park the bridge until the user explicitly starts a QR login again.
 *
 * Stops background timers and removes the previous QR image so a stale code is
 * never shown as current.
 */
function setNeedsLogin() {
  connectionState = 'needs_login';
  stopKeepAlive();
  stopSessionSave();
  try {
    if (existsSync(QR_PATH)) {
      unlinkSync(QR_PATH);
    }
  } catch {
    // A missing or locked QR file does not change the login state.
  }
}

function preventListenerRestart() {
  const listener = api?.listener;
  if (!listener) {
    return;
  }
  try {
    // zca-js schedules its own retryOnClose restart inside the socket close
    // handler. Replacing start() suppresses that timer for terminal codes.
    listener.start = () => {
      console.log('[zalo] listener restart suppressed after terminal close');
    };
  } catch {
    // A frozen listener object leaves the explicit stop() below as the guard.
  }
}

function applyTerminalClose(decision) {
  if (terminalCloseLogged) {
    return;
  }
  terminalCloseLogged = true;
  terminalClosed = true;
  setNeedsLogin();
  console.log(`[zalo] closed: ${decision.reason}; login required`);
  if (reloginTimer) {
    clearTimeout(reloginTimer);
    reloginTimer = null;
  }
  preventListenerRestart();
  try {
    api?.listener?.stop();
  } catch {
    // The socket may already be closed.
  }
}

function scheduleRelogin() {
  if (reloginTimer || shuttingDown || terminalClosed) {
    return;
  }
  if (reloginAttempt >= MAX_RELOGIN_ATTEMPTS) {
    console.log('[zalo] relogin limit reached; login required');
    setNeedsLogin();
    return;
  }
  reloginAttempt += 1;
  const delayMs = reloginDelayMs(reloginAttempt);
  console.log(
    `[zalo] relogin attempt ${reloginAttempt}/${MAX_RELOGIN_ATTEMPTS} in ${Math.round(
      delayMs / 1000,
    )}s`,
  );
  reloginTimer = setTimeout(() => {
    reloginTimer = null;
    void restartClient();
  }, delayMs);
  if (typeof reloginTimer.unref === 'function') {
    reloginTimer.unref();
  }
}

async function restartClient() {
  if (shuttingDown || terminalClosed) {
    return;
  }
  // Stopping the old listener emits a manual-close event; ignore it so the
  // shutdown does not schedule a second relogin of its own.
  suppressCloseEvents = true;
  try {
    api?.listener?.stop();
  } catch {
    // The socket may already be closed.
  }
  api = null;
  connectionState = 'connecting';
  try {
    await startClient({ allowQr: false });
  } catch {
    console.log('[zalo] relogin failed; login required');
    setNeedsLogin();
  } finally {
    const releaseTimer = setTimeout(() => {
      suppressCloseEvents = false;
    }, 5_000);
    if (typeof releaseTimer.unref === 'function') {
      releaseTimer.unref();
    }
  }
}

function handleListenerClose(code) {
  if (shuttingDown || suppressCloseEvents || terminalClosed) {
    return;
  }
  const decision = classifyCloseCode(code);
  if (decision.terminal) {
    applyTerminalClose(decision);
    return;
  }
  connectionState = 'closed';
  console.log(`[zalo] closed: code ${decision.code || 0}`);
  stopKeepAlive();
  stopSessionSave();
  scheduleRelogin();
}

function attachListener() {
  const listener = api.listener;
  listener.on('connected', () => {
    connectionState = 'connected';
    suppressCloseEvents = false;
    terminalClosed = false;
    terminalCloseLogged = false;
    reloginAttempt = 0;
    try {
      ownId = normalizeMessageId(api.getOwnId()) || ownId;
    } catch {
      // Keep the id learned during login when getOwnId is unavailable.
    }
    console.log('[zalo] connected');
    startKeepAlive();
    startSessionSave();
    requestCatchUp();
  });
  listener.on('message', (message) => {
    ingestMessage(message);
  });
  listener.on('old_messages', (messages, threadType) => {
    handleOldMessages(messages, threadType);
  });
  listener.on('disconnected', (code) => {
    const decision = classifyCloseCode(code);
    if (decision.terminal) {
      applyTerminalClose(decision);
      return;
    }
    stopKeepAlive();
    if (connectionState === 'connected') {
      connectionState = 'connecting';
      console.log('[zalo] disconnected; waiting for reconnect');
    }
  });
  listener.on('closed', (code) => {
    handleListenerClose(code);
  });
  listener.on('error', (error) => {
    console.log(`[zalo] listener error (${error?.name || 'Error'})`);
  });
  listener.start({ retryOnClose: true });
}

function handleQREvent(event, rememberCredentials) {
  switch (event.type) {
    case LoginQRCallbackEventType.QRCodeGenerated: {
      connectionState = 'qr';
      qrExpiredCount = 0;
      // Zalo's PNG encodes only the bare token; the mobile app needs the login URL.
      const token = typeof event.data?.token === 'string' ? event.data.token : '';
      const saved = token
        ? QRCode.toFile(QR_PATH, `http://zaloapp.com/qr/l?tk=${token}`, { width: 360 })
        : event.actions.saveToFile(QR_PATH);
      Promise.resolve(saved)
        .then(() => {
          console.log(`[zalo] QR saved to ${QR_PATH}`);
        })
        .catch(() => {
          console.log('[zalo] failed to save QR image');
        });
      break;
    }
    case LoginQRCallbackEventType.QRCodeExpired:
    case LoginQRCallbackEventType.QRCodeDeclined: {
      const decision = nextQrExpiryAttempt(qrExpiredCount);
      qrExpiredCount = decision.attempts;
      if (!decision.retry) {
        qrLoginAborted = true;
        setNeedsLogin();
        console.log('[zalo] QR expired too many times; login required');
        try {
          event.actions.abort();
        } catch {
          // The login request may already be closed.
        }
        break;
      }
      console.log('[zalo] QR expired or declined; requesting a new code');
      try {
        event.actions.retry();
      } catch {
        qrLoginAborted = true;
        setNeedsLogin();
        console.log('[zalo] QR retry failed; login required');
      }
      break;
    }
    case LoginQRCallbackEventType.QRCodeScanned:
      console.log('[zalo] QR scanned; confirm on your phone');
      break;
    case LoginQRCallbackEventType.GotLoginInfo:
      qrExpiredCount = 0;
      rememberCredentials({
        cookie: event.data?.cookie,
        imei: event.data?.imei,
        userAgent: event.data?.userAgent,
      });
      console.log('[zalo] login credentials received');
      break;
    default:
      break;
  }
}

async function loginWithQR(zalo) {
  let credentials = null;
  connectionState = 'connecting';
  api = await zalo.loginQR({ qrPath: QR_PATH }, (event) => {
    handleQREvent(event, (value) => {
      credentials = value;
    });
  });
  // GotLoginInfo fires before loginQR resolves, so the credentials captured in
  // the callback identify the session that just authenticated.
  return credentials;
}

function finishLogin(identity) {
  sessionIdentity = {
    imei: String(identity?.imei || ''),
    userAgent: String(identity?.userAgent || ''),
  };
  try {
    ownId = normalizeMessageId(api.getOwnId()) || ownId;
  } catch {
    ownId = ownId || '';
  }
  // Persist the current cookie jar (which may have been refreshed during
  // login) plus the original device identity.
  persistSessionFromApi();
  startSessionSave();
  attachListener();
}

async function startClient({ allowQr = false, forceQr = false } = {}) {
  mkdirSync(SESSION_DIR, { recursive: true, mode: 0o700 });
  trimHistory();
  const zalo = new Zalo({ logging: false });
  const session = forceQr ? null : loadSession();
  if (session) {
    try {
      console.log('[zalo] restoring saved session');
      api = await zalo.login({
        cookie: session.cookie,
        imei: session.imei,
        userAgent: session.userAgent,
      });
      finishLogin({ imei: session.imei, userAgent: session.userAgent });
      return;
    } catch (error) {
      console.log(`[zalo] saved session login failed (${error?.name || 'Error'})`);
      if (!allowQr) {
        throw error;
      }
    }
  } else if (!allowQr) {
    throw new Error('saved session unavailable');
  }
  const credentials = await loginWithQR(zalo);
  if (qrLoginAborted || !credentials) {
    throw new Error('QR login was not completed');
  }
  finishLogin(credentials);
}

/**
 * Start one on-demand QR login. The HTTP caller returns immediately; the
 * session state is observable through GET /health.
 */
async function beginQrLogin() {
  if (loginInFlight || connectionState === 'connected') {
    return;
  }
  loginInFlight = true;
  qrExpiredCount = 0;
  qrLoginAborted = false;
  terminalClosed = false;
  terminalCloseLogged = false;
  suppressCloseEvents = false;
  reloginAttempt = 0;
  if (reloginTimer) {
    clearTimeout(reloginTimer);
    reloginTimer = null;
  }
  connectionState = 'connecting';
  try {
    await startClient({ allowQr: true, forceQr: true });
  } catch {
    setNeedsLogin();
    console.log('[zalo] QR login failed; login required');
  } finally {
    loginInFlight = false;
  }
}

function isConnected() {
  return Boolean(api) && connectionState === 'connected';
}

function fail(res, status, error) {
  return res.status(status).json({ error });
}

function fromUpstreamError(res) {
  return fail(res, 502, 'upstream_failed');
}

// A throttled info read is reported separately so the adapter can back off
// instead of treating the account as broken.
function fromInfoError(res, error) {
  if (error?.code === 'rate_limited') {
    return fail(res, 429, 'rate_limited');
  }
  return fromUpstreamError(res);
}

const app = express();

app.use((req, res, next) => {
  if (!isLoopback(req.socket?.remoteAddress)) {
    return res.status(403).json({ error: 'Loopback clients only' });
  }
  return next();
});

// Authenticate before reading a body: only GET /health is open.
app.use((req, res, next) => {
  if (req.method === 'GET' && req.path === '/health') {
    return next();
  }
  const provided = parseBearerToken(req.get('authorization'));
  if (!provided || !constantTimeEqual(BRIDGE_TOKEN, provided)) {
    return fail(res, 401, 'unauthorized');
  }
  return next();
});

const jsonSmall = express.json({ limit: '64kb' });
const jsonMedia = express.json({ limit: '14mb' });
app.use((req, res, next) => {
  const parser = req.path === '/send-media' ? jsonMedia : jsonSmall;
  return parser(req, res, next);
});

app.get('/health', (req, res) => {
  res.json({
    status: connectionState,
    scriptHash: SCRIPT_HASH,
    ownId,
  });
});

app.post('/login', (req, res) => {
  if (connectionState === 'connected') {
    return res.status(409).json({ status: 'connected', error: 'Already connected' });
  }
  if (loginInFlight) {
    return res.status(202).json({ status: connectionState });
  }
  void beginQrLogin();
  return res.status(202).json({ status: 'connecting' });
});

app.get('/messages', (req, res) => {
  const messages = messageQueue.splice(0, messageQueue.length);
  return res.json(fillGroupNames(messages, groupNames));
});

app.get('/me', async (req, res) => {
  if (!isConnected()) {
    return fail(res, 503, 'not_connected');
  }
  if (!readBucket.take()) {
    return fail(res, 429, 'rate_limited');
  }
  try {
    const info = await infoGate.run('me', () => api.fetchAccountInfo(), {
      ttlMs: LIST_CACHE_TTL_MS,
    });
    const profile = info?.profile || {};
    let accountId = '';
    try {
      accountId = normalizeMessageId(api.getOwnId());
    } catch {
      accountId = '';
    }
    return res.json({
      userId: accountId || normalizeMessageId(profile.userId),
      displayName: String(profile.displayName || profile.zaloName || ''),
      avatar: String(profile.avatar || ''),
    });
  } catch (error) {
    return fromInfoError(res, error);
  }
});

app.get('/friends', async (req, res) => {
  if (!isConnected()) {
    return fail(res, 503, 'not_connected');
  }
  if (!readBucket.take()) {
    return fail(res, 429, 'rate_limited');
  }
  try {
    const friends = await infoGate.run('friends', () => api.getAllFriends(), {
      ttlMs: LIST_CACHE_TTL_MS,
    });
    return res.json({
      friends: (Array.isArray(friends) ? friends : []).map((friend) => ({
        userId: normalizeMessageId(friend?.userId),
        displayName: String(friend?.displayName || ''),
        zaloName: String(friend?.zaloName || ''),
      })),
    });
  } catch (error) {
    return fromInfoError(res, error);
  }
});

app.get('/groups', async (req, res) => {
  if (!isConnected()) {
    return fail(res, 503, 'not_connected');
  }
  if (!readBucket.take()) {
    return fail(res, 429, 'rate_limited');
  }
  try {
    const listing = await infoGate.run('groups:list', () => api.getAllGroups(), {
      ttlMs: LIST_CACHE_TTL_MS,
    });
    const grid = listing?.gridVerMap && typeof listing.gridVerMap === 'object'
      ? listing.gridVerMap
      : {};
    const groups = [];
    for (const batch of chunkList(Object.keys(grid), GROUP_INFO_BATCH_SIZE)) {
      const info = await infoGate.run(
        groupInfoCacheKey(batch),
        () => api.getGroupInfo(batch),
        { ttlMs: GROUP_INFO_CACHE_TTL_MS },
      );
      const gridInfoMap = info?.gridInfoMap || {};
      for (const groupId of batch) {
        const group = gridInfoMap[groupId];
        if (!group) {
          continue;
        }
        const name = String(group.name || '');
        rememberGroupName(groupNames, groupId, name);
        groups.push({
          groupId: normalizeMessageId(group.groupId || groupId),
          name,
          memberCount: Number(group.totalMember) || 0,
        });
      }
    }
    return res.json({ groups });
  } catch (error) {
    return fromInfoError(res, error);
  }
});

app.post('/send', async (req, res) => {
  const body = validateSendMessageBody(req.body);
  if (!body.ok) {
    return fail(res, 400, 'invalid_request');
  }
  if (!isConnected()) {
    return fail(res, 503, 'not_connected');
  }
  // Length validation applies to the raw input; Markdown is converted once,
  // then the clean text is chunked with the style spans split alongside it.
  const converted = markdownToZalo(body.message);
  const chunks = splitStyledChunks(converted.msg, converted.styles);
  if (chunks.length === 0) {
    return fail(res, 400, 'invalid_request');
  }
  // Every chunk is one outbound message and consumes one send credit.
  if (!sendBucket.take(chunks.length)) {
    return fail(res, 429, 'rate_limited');
  }
  const threadType = toZaloThreadType(body.threadType);
  const messageIds = [];
  try {
    for (const chunk of chunks) {
      // zca-js TextStyle: Bold "b", Italic "i", StrikeThrough "s".
      const content = chunk.styles.length
        ? { msg: chunk.msg, styles: chunk.styles }
        : { msg: chunk.msg };
      const sent = await api.sendMessage(content, body.chatId, threadType);
      const messageId = sent?.message?.msgId;
      messageIds.push(messageId === undefined || messageId === null ? null : String(messageId));
    }
  } catch {
    return fail(res, 500, 'send_failed');
  }
  return res.json({
    success: true,
    // The adapter treats the last chunk as the primary reply id.
    messageId: messageIds[messageIds.length - 1] ?? null,
    messageIds,
  });
});

app.post('/send-media', async (req, res) => {
  const body = validateSendMediaBody(req.body);
  if (!body.ok) {
    return fail(res, 400, 'invalid_request');
  }
  if (!isConnected()) {
    return fail(res, 503, 'not_connected');
  }
  if (!sendBucket.take()) {
    return fail(res, 429, 'rate_limited');
  }
  let tempDir = '';
  try {
    tempDir = mkdtempSync(path.join(tmpdir(), 'zalo-bridge-media-'));
    const filePath = path.join(tempDir, body.fileName);
    writeFileSync(filePath, body.buffer, { mode: 0o600 });
    const sent = await api.sendMessage(
      { msg: body.caption, attachments: [filePath] },
      body.chatId,
      toZaloThreadType(body.threadType),
    );
    const messageId = sent?.message?.msgId;
    return res.json({
      success: true,
      messageId: messageId === undefined || messageId === null ? null : String(messageId),
    });
  } catch {
    return fail(res, 500, 'send_failed');
  } finally {
    if (tempDir) {
      try {
        rmSync(tempDir, { recursive: true, force: true });
      } catch {
        // The private temp directory is removed on the next OS cleanup.
      }
    }
  }
});

app.post('/send-link', async (req, res) => {
  const body = validateSendLinkBody(req.body);
  if (!body.ok) {
    return fail(res, 400, 'invalid_request');
  }
  if (!isConnected()) {
    return fail(res, 503, 'not_connected');
  }
  if (!sendBucket.take()) {
    return fail(res, 429, 'rate_limited');
  }
  try {
    const sent = await api.sendLink(
      { msg: body.message, link: body.url },
      body.chatId,
      toZaloThreadType(body.threadType),
    );
    return res.json({
      success: true,
      messageId: sent?.msgId === undefined || sent?.msgId === null ? null : String(sent.msgId),
    });
  } catch {
    return fail(res, 500, 'send_failed');
  }
});

app.post('/react', async (req, res) => {
  const body = validateReactBody(req.body);
  if (!body.ok) {
    return fail(res, 400, 'invalid_request');
  }
  if (!isConnected()) {
    return fail(res, 503, 'not_connected');
  }
  const remembered = inboundMessages.get(body.messageId);
  if (!remembered) {
    return fail(res, 404, 'not_found');
  }
  const reaction = Reactions[body.reaction];
  if (!reaction) {
    return fail(res, 400, 'invalid_request');
  }
  if (!sendBucket.take()) {
    return fail(res, 429, 'rate_limited');
  }
  try {
    await api.addReaction(reaction, {
      data: { msgId: body.messageId, cliMsgId: remembered.cliMsgId },
      threadId: remembered.threadId,
      type: toZaloThreadType(remembered.threadType),
    });
    return res.json({ success: true });
  } catch {
    return fail(res, 500, 'send_failed');
  }
});

app.post('/typing', async (req, res) => {
  const body = validateTypingBody(req.body);
  if (!body.ok) {
    return fail(res, 400, 'invalid_request');
  }
  if (!isConnected()) {
    return fail(res, 503, 'not_connected');
  }
  try {
    await api.sendTypingEvent(body.chatId, toZaloThreadType(body.threadType));
  } catch {
    // Typing and receipt events are best effort.
  }
  return res.json({ success: true });
});

app.post('/seen', async (req, res) => {
  const body = validateSeenBody(req.body);
  if (!body.ok) {
    return fail(res, 400, 'invalid_request');
  }
  if (!isConnected()) {
    return fail(res, 503, 'not_connected');
  }
  const remembered = inboundMessages.get(body.messageId);
  if (!remembered?.receipt) {
    return fail(res, 404, 'not_found');
  }
  const threadType = toZaloThreadType(remembered.threadType);
  try {
    await api.sendDeliveredEvent(false, remembered.receipt, threadType);
  } catch {
    // Best effort.
  }
  try {
    await api.sendSeenEvent(remembered.receipt, threadType);
  } catch {
    // Best effort.
  }
  return res.json({ success: true });
});

// Normalize JSON parse and body-size failures to the shared error envelope.
app.use((error, req, res, next) => {
  if (!error) {
    return next();
  }
  if (error.type === 'entity.too.large') {
    return fail(res, 413, 'invalid_request');
  }
  return fail(res, 400, 'invalid_request');
});

process.on('SIGTERM', () => {
  shuttingDown = true;
  stopKeepAlive();
  stopSessionSave();
  if (reloginTimer) {
    clearTimeout(reloginTimer);
    reloginTimer = null;
  }
  try {
    api?.listener?.stop();
  } catch {
    // Already stopped.
  }
  process.exit(0);
});

app.listen(PORT, '127.0.0.1', () => {
  writeBridgeToken();
  console.log(`[zalo] bridge listening on 127.0.0.1:${PORT}`);
  console.log(`[zalo] session directory: ${SESSION_DIR}`);
  console.log(`[zalo] inbound queue limit: ${MAX_QUEUE_SIZE}`);
  if (!loadSession()) {
    setNeedsLogin();
    console.log('[zalo] no saved session; waiting for POST /login');
    return;
  }
  startClient().catch(() => {
    setNeedsLogin();
    console.log('[zalo] saved session is not usable; login required');
  });
});
