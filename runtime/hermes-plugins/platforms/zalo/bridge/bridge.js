#!/usr/bin/env node
/**
 * Zalo personal-account bridge for the XNOBrain/Hermes runtime.
 *
 * Standalone Node.js process that logs into Zalo through zca-js and exposes a
 * loopback-only HTTP API for the Python platform adapter.
 *
 * Endpoints:
 *   GET  /health   - {status, scriptHash, ownId}
 *   GET  /messages - drain queued inbound direct messages
 *   POST /send     - {chatId, message} -> {success, messageId}
 *
 * Usage:
 *   node bridge.js --port 3110 --session <HERMES_HOME>/zalo
 *
 * The bridge only handles direct text messages. Group chats, media, reactions
 * and self-echoes are intentionally ignored in this first slice.
 */

import express from 'express';
import QRCode from 'qrcode';
import path from 'path';
import { homedir } from 'os';
import { createHash } from 'crypto';
import { fileURLToPath } from 'url';
import {
  appendFileSync,
  chmodSync,
  closeSync,
  existsSync,
  fsyncSync,
  mkdirSync,
  openSync,
  readFileSync,
  renameSync,
  writeFileSync,
} from 'fs';
import {
  LoginQRCallbackEventType,
  ThreadType,
  Zalo,
} from 'zca-js';

import {
  MAX_QUEUE_SIZE,
  MAX_RELOGIN_ATTEMPTS,
  classifyCloseCode,
  cookieListFromJar,
  historyEntry,
  isLoopback,
  isNewerMessageId,
  normalizeMessageId,
  pushToQueue,
  reloginDelayMs,
  rememberCursor,
  shouldQueueMessage,
  trimHistoryLines,
  validateSendBody,
} from './lib.js';

const DEFAULT_PORT = 3110;
const KEEPALIVE_INTERVAL_MS = 60_000;
const SESSION_SAVE_INTERVAL_MS = 10 * 60_000;
const MAX_SEEN_IDS = 2_000;

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

let SCRIPT_HASH = '';
try {
  SCRIPT_HASH = createHash('sha256')
    .update(readFileSync(fileURLToPath(import.meta.url)))
    .digest('hex')
    .slice(0, 16);
} catch {
  // /health simply reports an empty hash when the script cannot be read.
}

// status: "connecting" | "qr" | "connected" | "closed"
let connectionState = 'connecting';
let api = null;
let ownId = '';
let shuttingDown = false;
let suppressCloseEvents = false;
let terminalClosed = false;
let terminalCloseLogged = false;
let reloginAttempt = 0;
let reloginTimer = null;
let reloginAllowQr = false;
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

function ingestMessage(message, { requireNewer = false } = {}) {
  if (!message || message.type !== ThreadType.User) {
    return false;
  }
  if (message.isSelf) {
    return false;
  }
  const data = message.data || {};
  if (typeof data.content !== 'string' || !data.content.trim()) {
    return false;
  }
  const threadId = normalizeMessageId(message.threadId || data.uidFrom);
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
  pushToQueue(messageQueue, {
    messageId,
    threadId,
    senderId,
    senderName,
    text: data.content,
    timestamp,
  });
  appendHistory(
    {
      messageId,
      threadId,
      senderId,
      senderName,
      text: data.content,
      timestamp,
    },
    { recovered: Boolean(requireNewer) },
  );
  return true;
}

function handleOldMessages(messages, threadType) {
  if (threadType !== ThreadType.User || !Array.isArray(messages)) {
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
  connectionState = 'closed';
  console.log(`[zalo] closed: ${decision.reason}`);
  if (reloginTimer) {
    clearTimeout(reloginTimer);
    reloginTimer = null;
  }
  stopKeepAlive();
  stopSessionSave();
  preventListenerRestart();
  try {
    api?.listener?.stop();
  } catch {
    // The socket may already be closed.
  }
}

function scheduleRelogin(allowQr = false) {
  if (reloginTimer || shuttingDown || terminalClosed) {
    return;
  }
  if (reloginAttempt >= MAX_RELOGIN_ATTEMPTS) {
    console.log('[zalo] relogin limit reached; restart the bridge to recover');
    return;
  }
  reloginAllowQr = allowQr;
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
    await startClient({ allowQr: reloginAllowQr });
  } catch {
    connectionState = 'closed';
    console.log('[zalo] relogin failed');
    scheduleRelogin(reloginAllowQr);
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
      console.log('[zalo] QR expired; requesting a new code');
      try {
        event.actions.retry();
      } catch {
        console.log('[zalo] QR retry failed');
      }
      break;
    case LoginQRCallbackEventType.QRCodeScanned:
      console.log('[zalo] QR scanned; confirm on your phone');
      break;
    case LoginQRCallbackEventType.QRCodeDeclined:
      console.log('[zalo] QR login declined; requesting a new code');
      try {
        event.actions.retry();
      } catch {
        console.log('[zalo] QR retry failed');
      }
      break;
    case LoginQRCallbackEventType.GotLoginInfo:
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
  connectionState = 'qr';
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

async function startClient({ allowQr = true } = {}) {
  mkdirSync(SESSION_DIR, { recursive: true, mode: 0o700 });
  trimHistory();
  const zalo = new Zalo({ logging: false });
  const session = loadSession();
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
      console.log(`[zalo] saved session login failed (${error?.name || 'Error'}); using QR`);
      if (!allowQr) {
        throw error;
      }
    }
  }
  if (!allowQr) {
    throw new Error('saved session unavailable');
  }
  const credentials = await loginWithQR(zalo);
  finishLogin(credentials);
}

const app = express();
app.use(express.json({ limit: '64kb' }));

app.use((req, res, next) => {
  if (!isLoopback(req.socket?.remoteAddress)) {
    return res.status(403).json({ error: 'Loopback clients only' });
  }
  return next();
});

app.get('/health', (req, res) => {
  res.json({
    status: connectionState,
    scriptHash: SCRIPT_HASH,
    ownId,
  });
});

app.get('/messages', (req, res) => {
  const messages = messageQueue.splice(0, messageQueue.length);
  res.json(messages);
});

app.post('/send', async (req, res) => {
  const body = validateSendBody(req.body);
  if (!body.ok) {
    return res.status(400).json({ error: body.error });
  }
  if (!api || connectionState !== 'connected') {
    return res.status(503).json({ error: 'Not connected to Zalo' });
  }
  try {
    const sent = await api.sendMessage(body.message, body.chatId, ThreadType.User);
    const messageId = sent?.message?.msgId;
    return res.json({
      success: true,
      messageId: messageId === undefined || messageId === null ? null : String(messageId),
    });
  } catch {
    return res.status(500).json({ error: 'Failed to send Zalo message' });
  }
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
  console.log(`[zalo] bridge listening on 127.0.0.1:${PORT}`);
  console.log(`[zalo] session directory: ${SESSION_DIR}`);
  console.log(`[zalo] inbound queue limit: ${MAX_QUEUE_SIZE}`);
  startClient().catch(() => {
    connectionState = 'closed';
    console.log('[zalo] login failed; scheduling bounded relogin');
    scheduleRelogin(true);
  });
});
