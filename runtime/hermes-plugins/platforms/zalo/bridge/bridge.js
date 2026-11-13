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
 *   GET  /conversations - direct and group conversations with pin/archive flags
 *   POST /history     - stored direct-message history, with a best-effort backfill
 *   POST /group-history - group history through getGroupChatHistory
 *   POST /search      - substring search over stored history
 *   POST /group-info  - one group and a member preview
 *   POST /user-info   - one user profile by id, phone, or username
 *   GET  /unread      - unread conversation marks
 *   POST /reminders   - group reminders
 *   POST /notes      - group notes
 *   POST /poll-detail - one poll
 *   POST /poll-create - a group poll
 *   POST /note-create - a group note
 *   POST /reminder-create - a reminder in a direct message or group
 *   POST /pin        - pin or unpin a conversation
 *   POST /mute       - mute or unmute a conversation
 *   GET  /labels     - conversation labels
 *   POST /labels     - add or remove one conversation from a label
 *   POST /unread-mark - add or remove an unread mark
 *   POST /group-members - list, add, or remove group members
 *   POST /group-settings - rename a group or change its settings
 *   POST /group-roles - add or remove a group deputy
 *   POST /group-pending - list, approve, or reject join requests
 *   POST /group-link - read, enable, or disable a group invite link
 *   POST /create-group - create a group with known members
 *   POST /friend-requests - list, accept, reject, send, or undo friend requests
 *   POST /friend-alias - list, set, or remove a friend alias
 *   POST /quick-reply - list, add, update, or remove quick messages
 *   POST /send        - chunked text message (optional quote and mentions)
 *   POST /send-media  - image, file, video, or voice attachment
 *   POST /send-link   - link preview
 *   POST /send-sticker - sticker search then send
 *   POST /send-card   - contact card for one visible user
 *   POST /forward     - forward one stored message
 *   POST /undo        - undo one message this account sent
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
 * Every inbound and outbound message is appended to history.jsonl, direct and
 * group alike, with 30-day retention and mode 0600.
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
  MuteAction,
  MuteDuration,
  Reactions,
  ReminderRepeatMode,
  ThreadType,
  Zalo,
} from 'zca-js';

import {
  GROUP_HISTORY_RATE_LIMIT_PER_MINUTE,
  GROUP_INFO_BATCH_SIZE,
  GROUP_INFO_CACHE_TTL_MS,
  LIST_CACHE_TTL_MS,
  MAX_HISTORY_READ_LIMIT,
  MAX_QUEUE_SIZE,
  MAX_RELOGIN_ATTEMPTS,
  MAX_SEARCH_SCAN,
  MAX_STICKER_SEARCH_LIMIT,
  READ_RATE_LIMIT_PER_MINUTE,
  SEND_RATE_LIMIT_PER_MINUTE,
  aliasRecords,
  applyLabelChange,
  chunkList,
  classifyCloseCode,
  constantTimeEqual,
  conversationEntries,
  conversationIds,
  cookieListFromJar,
  createInfoGate,
  createTokenBucket,
  fillGroupNames,
  groupHistoryRecords,
  groupInfoCacheKey,
  groupInfoRecord,
  groupMembersRecord,
  groupSettingsOptions,
  groupSettingsRecord,
  historyEntry,
  inboundQueueItem,
  isLoopback,
  isNewerMessageId,
  labelEntries,
  markdownToZalo,
  mediaFromContent,
  memberChangeRecord,
  mentionPayloads,
  mentionsSelf,
  messageRef,
  messageText,
  nextQrExpiryAttempt,
  normalizeMessageId,
  noteRecord,
  noteRecords,
  parseBearerToken,
  pendingMemberRecords,
  pollRecord,
  pushToQueue,
  quickReplyRecords,
  quotePayload,
  quotesSelf,
  readHistoryRecords,
  receivedFriendRequestRecords,
  receiptFields,
  reloginDelayMs,
  rememberCursor,
  rememberGroupName,
  reminderRecords,
  reviewResults,
  selectHistoryRecords,
  sentFriendRequestRecords,
  shouldQueueMessage,
  splitStyledChunks,
  trimHistoryLines,
  undoExpired,
  unreadRecords,
  userInfoRecord,
  validateBoardBody,
  validateCreateGroupBody,
  validateForwardBody,
  validateFriendAliasBody,
  validateFriendRequestsBody,
  validateGroupLinkBody,
  validateGroupMembersBody,
  validateGroupPendingBody,
  validateGroupRolesBody,
  validateGroupSettingsBody,
  validateQuickReplyBody,
  validateGroupHistoryBody,
  validateGroupInfoBody,
  validateHistoryBody,
  validateLabelsBody,
  validateMuteBody,
  validateNoteCreateBody,
  validatePinBody,
  validatePollCreateBody,
  validatePollDetailBody,
  validateReactBody,
  validateReminderCreateBody,
  validateSearchBody,
  validateSeenBody,
  validateSendCardBody,
  validateSendLinkBody,
  validateSendMediaBody,
  validateSendMessageBody,
  validateSendStickerBody,
  validateTypingBody,
  validateUndoBody,
  validateUnreadMarkBody,
  validateUserInfoBody,
} from './lib.js';

const DEFAULT_PORT = 3110;
const KEEPALIVE_INTERVAL_MS = 60_000;
const SESSION_SAVE_INTERVAL_MS = 10 * 60_000;
const HISTORY_TRIM_INTERVAL_MS = 6 * 60 * 60_000;
/** How long one DM history request waits for `old_messages` to arrive. */
const BACKFILL_WAIT_MS = 2_500;
const MAX_BACKFILL_MESSAGES = 500;
const MAX_SEEN_IDS = 2_000;
const MAX_REMEMBERED_MESSAGES = 500;
/** Bounded message-id to cliMsgId memory used for quotes and undo. */
const MAX_MESSAGE_REFS = 2_000;
/** How long a quote or undo waits for the live echo of a just-sent message. */
const MESSAGE_REF_WAIT_MS = 2_000;

/** Bridge duration/repeat names mapped onto the zca-js enums. */
const MUTE_DURATIONS = Object.freeze({
  one_hour: MuteDuration.ONE_HOUR,
  four_hours: MuteDuration.FOUR_HOURS,
  forever: MuteDuration.FOREVER,
  until_8am: MuteDuration.UNTIL_8AM,
});
const REMINDER_REPEATS = Object.freeze({
  none: ReminderRepeatMode.None,
  daily: ReminderRepeatMode.Daily,
  weekly: ReminderRepeatMode.Weekly,
  monthly: ReminderRepeatMode.Monthly,
});

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
// Group history is the heaviest read endpoint and gets its own smaller bucket.
const groupHistoryBucket = createTokenBucket({
  capacity: GROUP_HISTORY_RATE_LIMIT_PER_MINUTE,
  perMinute: GROUP_HISTORY_RATE_LIMIT_PER_MINUTE,
});
// One gate serializes every upstream read-info call, so Zalo never sees two
// profile/list/group-info requests close together. It also owns the TTL cache
// shared by /me, /friends, /groups and the inbound group-name lookups.
const infoGate = createInfoGate();

// Recently received inbound messages, used to resolve reaction and receipt
// targets. Bounded so a long-running bridge cannot grow without limit.
const inboundMessages = new Map();
// Live message identity for quote replies and undo, keyed by thread and id.
const messageRefs = new Map();
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
let historyTrimTimer = null;
let sessionIdentity = null;

const messageQueue = [];
const seenIds = new Set();
const seenOrder = [];
const cursors = loadJsonFile(CURSOR_FILE, {});
// One in-flight direct-message backfill; old messages never join the queue.
let backfillState = null;

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

function appendHistory(
  message,
  { recovered = false, threadType = 'user', direction = 'in', type = 'text' } = {},
) {
  const entry = historyEntry({
    messageId: message.messageId,
    threadId: message.threadId,
    senderId: message.senderId,
    senderName: message.senderName,
    text: message.text,
    timestamp: message.timestamp,
    threadType: message.threadType || threadType,
    direction: message.direction || direction,
    type: message.type || type,
    recovered,
  });
  try {
    appendFileSync(HISTORY_FILE, `${JSON.stringify(entry)}\n`, {
      encoding: 'utf8',
      mode: 0o600,
    });
    // `mode` only applies to a new file; force the mode on every append.
    try {
      chmodSync(HISTORY_FILE, 0o600);
    } catch {
      // Best effort on filesystems without POSIX modes.
    }
  } catch {
    console.log('[zalo] failed to append history');
  }
}

/** Read the stored history file as raw JSONL lines; missing file reads empty. */
function readHistoryLines() {
  try {
    return readFileSync(HISTORY_FILE, 'utf8').split(/\r?\n/);
  } catch {
    return [];
  }
}

/** Convert an optional `sinceHours` value into an absolute epoch threshold. */
function sinceThreshold(sinceHours) {
  if (typeof sinceHours !== 'number' || !Number.isFinite(sinceHours)) {
    return null;
  }
  return Date.now() - sinceHours * 3_600_000;
}

/**
 * Keep only the last 30 days of stored history. Runs once at bridge start and
 * every six hours so the append-only file cannot grow without bound.
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
    console.log('[zalo] failed to trim history');
  }
}

function startHistoryTrim() {
  if (historyTrimTimer) {
    clearInterval(historyTrimTimer);
  }
  historyTrimTimer = setInterval(trimHistory, HISTORY_TRIM_INTERVAL_MS);
  if (typeof historyTrimTimer.unref === 'function') {
    historyTrimTimer.unref();
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

function messageRefKey(threadId, messageId) {
  return `${normalizeMessageId(threadId)}:${normalizeMessageId(messageId)}`;
}

/** Remember one live message so a later quote or undo can find its ids. */
function rememberMessageRef(reference) {
  if (!reference) {
    return;
  }
  const key = messageRefKey(reference.threadId, reference.msgId);
  messageRefs.delete(key);
  messageRefs.set(key, reference);
  while (messageRefs.size > MAX_MESSAGE_REFS) {
    messageRefs.delete(messageRefs.keys().next().value);
  }
}

function readMessageRef(threadId, messageId) {
  return messageRefs.get(messageRefKey(threadId, messageId));
}

/** Remember every message of one `getGroupChatHistory` page. */
function rememberGroupHistoryRefs(groupId, messages) {
  for (const message of Array.isArray(messages) ? messages : []) {
    rememberMessageRef(
      messageRef(message?.data, {
        threadId: groupId,
        threadType: 'group',
        isSelf: message?.isSelf,
      }),
    );
  }
}

/** Remember the identity of one live listener message, self or inbound. */
function rememberLiveMessage(message) {
  if (
    !message ||
    (message.type !== ThreadType.User && message.type !== ThreadType.Group)
  ) {
    return;
  }
  const data = message.data || {};
  const threadType = threadTypeName(message.type);
  const threadId = normalizeMessageId(
    message.threadId || (threadType === 'user' ? data.uidFrom : ''),
  );
  rememberMessageRef(
    messageRef(data, {
      threadId,
      threadType,
      isSelf: Boolean(message.isSelf),
    }),
  );
}

function delay(ms) {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    if (typeof timer.unref === 'function') {
      timer.unref();
    }
  });
}

/**
 * Find the identity of one stored message.
 *
 * The live listener memory answers most quotes; group messages can always be
 * looked up again through `getGroupChatHistory`. A just-sent message may not
 * have echoed yet, so the lookup waits briefly before giving up.
 */
async function findMessageRef(threadId, threadType, messageId) {
  const deadline = Date.now() + MESSAGE_REF_WAIT_MS;
  let reference = readMessageRef(threadId, messageId);
  while (!reference && Date.now() < deadline) {
    await delay(100);
    reference = readMessageRef(threadId, messageId);
  }
  if (reference || threadType !== 'group' || !isConnected()) {
    return reference || null;
  }
  if (!groupHistoryBucket.take()) {
    const error = new Error('rate_limited');
    error.code = 'rate_limited';
    throw error;
  }
  const history = await api.getGroupChatHistory(threadId, MAX_HISTORY_READ_LIMIT);
  rememberGroupHistoryRefs(threadId, history?.groupMsgs);
  return readMessageRef(threadId, messageId) || null;
}

/** Find one stored history record for a thread and message id. */
function storedMessage(threadId, messageId) {
  const thread = normalizeMessageId(threadId);
  const id = normalizeMessageId(messageId);
  return (
    readHistoryRecords(readHistoryLines()).find(
      (record) => record.threadId === thread && record.messageId === id,
    ) || null
  );
}

/**
 * Resolve the display names of the requested group members.
 *
 * Returns null when any id is not a member of the group, so a mention can
 * never point at somebody outside the conversation.
 */
async function groupMentionMembers(groupId, uids) {
  const info = await infoGate.run(
    groupInfoCacheKey([groupId]),
    () => api.getGroupInfo([groupId]),
    { ttlMs: GROUP_INFO_CACHE_TTL_MS },
  );
  const group = info?.gridInfoMap?.[groupId];
  if (!group) {
    return null;
  }
  const memberIds = new Set(
    (Array.isArray(group.memberIds) ? group.memberIds : []).map(normalizeMessageId),
  );
  for (const uid of uids) {
    if (!memberIds.has(uid)) {
      return null;
    }
  }
  const profiles = await infoGate.run(
    `groupmembers:${groupId}:${uids.join(',')}`,
    () => api.getGroupMembersInfo(uids),
    { ttlMs: GROUP_INFO_CACHE_TTL_MS },
  );
  const map = profiles?.profiles || {};
  return uids.map((uid) => ({
    uid,
    name: String(map[uid]?.displayName || map[uid]?.zaloName || ''),
  }));
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
  appendHistory(
    {
      messageId,
      threadId,
      senderId,
      senderName,
      text,
      timestamp,
      threadType,
      direction: 'in',
      type: media.length ? media[0].kind : 'text',
    },
    { recovered: Boolean(requireNewer) },
  );
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
    const threadId = normalizeMessageId(
      message?.threadId ||
        (threadType === ThreadType.User ? message?.data?.uidFrom : ''),
    );
    rememberMessageRef(
      messageRef(message?.data, {
        threadId,
        threadType: threadTypeName(threadType),
        isSelf: message?.isSelf,
      }),
    );
    if (
      threadType === ThreadType.User &&
      backfillState &&
      backfillState.threadId === threadId
    ) {
      collectBackfillMessage(message, threadId);
      continue;
    }
    ingestMessage(message, { requireNewer: true });
  }
}

/**
 * Store one older direct message collected for an in-flight history request.
 *
 * Backfilled messages never enter the inbound queue: they are history only, so
 * a read cannot start an agent turn.
 */
function collectBackfillMessage(message, threadId) {
  if (!backfillState || backfillState.collected.length >= MAX_BACKFILL_MESSAGES) {
    return;
  }
  const data = message?.data || {};
  const messageId = normalizeMessageId(data.msgId ?? data.realMsgId);
  if (!messageId) {
    return;
  }
  const media = mediaFromContent(data.content);
  const text = messageText(data.content);
  if (!text.trim() && media.length === 0) {
    return;
  }
  rememberMessageRef(
    messageRef(data, {
      threadId,
      threadType: 'user',
      isSelf: Boolean(message?.isSelf),
    }),
  );
  backfillState.collected.push({
    messageId,
    threadId,
    senderId: normalizeMessageId(data.uidFrom || threadId),
    senderName: String(data.dName || ''),
    text,
    timestamp: Number(data.ts) || Date.now(),
    threadType: 'user',
    direction: message?.isSelf ? 'out' : 'in',
    type: media.length ? media[0].kind : 'text',
  });
}

/**
 * Request one page of older direct messages for one thread and store them.
 *
 * zca-js `requestOldMessages` has no per-chat cursor, so this asks for the page
 * before the oldest stored message and keeps only the events for this thread.
 * Resolves true when at least one message was stored.
 */
function backfillThread(threadId, lastMessageId, limit) {
  if (!api?.listener || backfillState) {
    return Promise.resolve(false);
  }
  return new Promise((resolve) => {
    const deadline = Date.now() + BACKFILL_WAIT_MS;
    const state = {
      threadId,
      collected: [],
      finish: null,
    };
    const timer = setInterval(() => {
      if (backfillState !== state) {
        return;
      }
      if (
        state.collected.length >= Math.min(limit, MAX_BACKFILL_MESSAGES) ||
        Date.now() >= deadline
      ) {
        state.finish();
      }
    }, 100);
    if (typeof timer.unref === 'function') {
      timer.unref();
    }
    state.finish = () => {
      if (backfillState !== state) {
        return;
      }
      backfillState = null;
      clearInterval(timer);
      const existing = new Set(
        readHistoryRecords(readHistoryLines())
          .filter((record) => record.threadId === threadId)
          .map((record) => record.messageId),
      );
      let stored = 0;
      for (const item of state.collected) {
        if (existing.has(item.messageId)) {
          continue;
        }
        existing.add(item.messageId);
        appendHistory(item, { recovered: true });
        stored += 1;
      }
      resolve(stored > 0);
    };
    backfillState = state;
    try {
      api.listener.requestOldMessages(ThreadType.User, lastMessageId || null);
    } catch {
      state.finish();
    }
  });
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
    rememberLiveMessage(message);
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

function friendEntries(friends) {
  return (Array.isArray(friends) ? friends : []).map((friend) => ({
    userId: normalizeMessageId(friend?.userId),
    displayName: String(friend?.displayName || ''),
    zaloName: String(friend?.zaloName || ''),
  }));
}

/** Load the group list with names and member counts, through the info gate. */
async function loadGroups() {
  const listing = await infoGate.run('groups:list', () => api.getAllGroups(), {
    ttlMs: LIST_CACHE_TTL_MS,
  });
  const grid =
    listing?.gridVerMap && typeof listing.gridVerMap === 'object'
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
  return groups;
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
    return res.json({ friends: friendEntries(friends) });
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
    return res.json({ groups: await loadGroups() });
  } catch (error) {
    return fromInfoError(res, error);
  }
});

app.get('/conversations', async (req, res) => {
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
    const groups = await loadGroups();
    const pinned = await infoGate.run(
      'conversations:pinned',
      () => api.getPinConversations(),
      { ttlMs: LIST_CACHE_TTL_MS },
    );
    const archived = await infoGate.run(
      'conversations:archived',
      () => api.getArchivedChatList(),
      { ttlMs: LIST_CACHE_TTL_MS },
    );
    return res.json({
      conversations: conversationEntries({
        friends: friendEntries(friends),
        groups,
        pinned: conversationIds(pinned?.conversations),
        archived: conversationIds(archived?.items),
      }),
    });
  } catch (error) {
    return fromInfoError(res, error);
  }
});

app.post('/history', async (req, res) => {
  const body = validateHistoryBody(req.body);
  if (!body.ok) {
    return fail(res, 400, 'invalid_request');
  }
  if (!readBucket.take()) {
    return fail(res, 429, 'rate_limited');
  }
  const read = () =>
    selectHistoryRecords(readHistoryRecords(readHistoryLines()), {
      threadIds: [body.chatId],
      since: sinceThreshold(body.sinceHours),
      limit: body.limit,
    });
  let result = read();
  let backfilled = false;
  if (body.backfill && result.records.length < body.limit && isConnected()) {
    const oldest = result.records[0]?.messageId || '';
    backfilled = await backfillThread(body.chatId, oldest, body.limit);
    if (backfilled) {
      result = read();
    }
  }
  return res.json({
    messages: result.records,
    truncated: result.matched > result.records.length,
    backfilled,
  });
});

app.post('/group-history', async (req, res) => {
  const body = validateGroupHistoryBody(req.body);
  if (!body.ok) {
    return fail(res, 400, 'invalid_request');
  }
  if (!isConnected()) {
    return fail(res, 503, 'not_connected');
  }
  // Its own bucket: the heaviest upstream read has the smallest allowance.
  if (!groupHistoryBucket.take()) {
    return fail(res, 429, 'rate_limited');
  }
  try {
    const history = await api.getGroupChatHistory(body.groupId, body.limit);
    rememberGroupHistoryRefs(body.groupId, history?.groupMsgs);
    return res.json({
      messages: groupHistoryRecords(history?.groupMsgs),
      more: Number(history?.more) || 0,
      last_action_id: normalizeMessageId(history?.lastActionId),
    });
  } catch (error) {
    return fromInfoError(res, error);
  }
});

app.post('/search', (req, res) => {
  const body = validateSearchBody(req.body);
  if (!body.ok) {
    return fail(res, 400, 'invalid_request');
  }
  if (!readBucket.take()) {
    return fail(res, 429, 'rate_limited');
  }
  const scanned = readHistoryRecords(readHistoryLines()).slice(-MAX_SEARCH_SCAN);
  const result = selectHistoryRecords(scanned, {
    threadIds: body.chatIds,
    query: body.query,
    since: sinceThreshold(body.sinceHours),
    limit: body.limit,
  });
  return res.json({
    messages: result.records,
    truncated: result.matched > result.records.length,
  });
});

app.post('/group-info', async (req, res) => {
  const body = validateGroupInfoBody(req.body);
  if (!body.ok) {
    return fail(res, 400, 'invalid_request');
  }
  if (!isConnected()) {
    return fail(res, 503, 'not_connected');
  }
  if (!readBucket.take()) {
    return fail(res, 429, 'rate_limited');
  }
  try {
    const info = await infoGate.run(
      groupInfoCacheKey([body.groupId]),
      () => api.getGroupInfo([body.groupId]),
      { ttlMs: GROUP_INFO_CACHE_TTL_MS },
    );
    const group = info?.gridInfoMap?.[body.groupId];
    if (!group) {
      return fail(res, 404, 'group_not_found');
    }
    let profiles = null;
    const memberIds = Array.isArray(group.memberIds)
      ? group.memberIds.slice(0, body.memberLimit)
      : [];
    if (memberIds.length) {
      const key = `groupmembers:${body.groupId}:${memberIds.join(',')}`;
      profiles = await infoGate.run(
        key,
        () => api.getGroupMembersInfo(memberIds),
        { ttlMs: GROUP_INFO_CACHE_TTL_MS },
      );
    }
    return res.json(
      groupInfoRecord(group, profiles?.profiles, {
        memberLimit: body.memberLimit,
      }),
    );
  } catch (error) {
    return fromInfoError(res, error);
  }
});

app.post('/user-info', async (req, res) => {
  const body = validateUserInfoBody(req.body);
  if (!body.ok) {
    return fail(res, 400, 'invalid_request');
  }
  if (!isConnected()) {
    return fail(res, 503, 'not_connected');
  }
  if (!readBucket.take()) {
    return fail(res, 429, 'rate_limited');
  }
  try {
    const key = `userinfo:${body.field}:${body.value}`;
    const loaded = await infoGate.run(
      key,
      async () => {
        if (body.field === 'userId') {
          const response = await api.getUserInfo(body.value);
          const changed = response?.changed_profiles || {};
          return changed[body.value] || Object.values(changed)[0] || null;
        }
        if (body.field === 'phone') {
          return api.findUser(body.value);
        }
        return api.findUserByUsername(body.value);
      },
      { ttlMs: LIST_CACHE_TTL_MS },
    );
    if (!loaded) {
      return fail(res, 404, 'user_not_found');
    }
    return res.json(userInfoRecord(loaded));
  } catch (error) {
    return fromInfoError(res, error);
  }
});

app.get('/unread', async (req, res) => {
  if (!isConnected()) {
    return fail(res, 503, 'not_connected');
  }
  if (!readBucket.take()) {
    return fail(res, 429, 'rate_limited');
  }
  try {
    const mark = await api.getUnreadMark();
    return res.json(unreadRecords(mark));
  } catch (error) {
    return fromInfoError(res, error);
  }
});

app.post('/reminders', async (req, res) => {
  const body = validateBoardBody(req.body);
  if (!body.ok) {
    return fail(res, 400, 'invalid_request');
  }
  if (!isConnected()) {
    return fail(res, 503, 'not_connected');
  }
  if (!readBucket.take()) {
    return fail(res, 429, 'rate_limited');
  }
  try {
    const items = await api.getListReminder(
      { page: body.page, count: body.count },
      body.groupId,
      ThreadType.Group,
    );
    return res.json({ reminders: reminderRecords(items) });
  } catch (error) {
    return fromInfoError(res, error);
  }
});

app.post('/notes', async (req, res) => {
  const body = validateBoardBody(req.body);
  if (!body.ok) {
    return fail(res, 400, 'invalid_request');
  }
  if (!isConnected()) {
    return fail(res, 503, 'not_connected');
  }
  if (!readBucket.take()) {
    return fail(res, 429, 'rate_limited');
  }
  try {
    const board = await api.getListBoard(
      { page: body.page, count: body.count },
      body.groupId,
    );
    return res.json({ notes: noteRecords(board?.items) });
  } catch (error) {
    return fromInfoError(res, error);
  }
});

app.post('/poll-detail', async (req, res) => {
  const body = validatePollDetailBody(req.body);
  if (!body.ok) {
    return fail(res, 400, 'invalid_request');
  }
  if (!isConnected()) {
    return fail(res, 503, 'not_connected');
  }
  if (!readBucket.take()) {
    return fail(res, 429, 'rate_limited');
  }
  try {
    const poll = await api.getPollDetail(Number(body.pollId));
    return res.json(pollRecord(poll));
  } catch (error) {
    return fromInfoError(res, error);
  }
});

app.post('/poll-create', async (req, res) => {
  const body = validatePollCreateBody(req.body);
  if (!body.ok) {
    return fail(res, 400, 'invalid_request');
  }
  if (!isConnected()) {
    return fail(res, 503, 'not_connected');
  }
  if (!sendBucket.take()) {
    return fail(res, 429, 'rate_limited');
  }
  const options = {
    question: body.question,
    options: body.options,
    allowMultiChoices: body.allowMultiChoices,
    allowAddNewOption: body.allowAddNewOption,
    hideVotePreview: body.hideVotePreview,
    isAnonymous: body.isAnonymous,
  };
  if (body.expiresInHours > 0) {
    options.expiredTime = Date.now() + body.expiresInHours * 3_600_000;
  }
  try {
    const poll = await api.createPoll(options, body.groupId);
    return res.json({ success: true, poll: pollRecord(poll) });
  } catch {
    return fail(res, 500, 'send_failed');
  }
});

app.post('/note-create', async (req, res) => {
  const body = validateNoteCreateBody(req.body);
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
    // zca-js 2.2.0 `createNote` takes a title only; it has no note body field.
    const note = await api.createNote(
      { title: body.title, pinAct: body.pinned },
      body.groupId,
    );
    return res.json({ success: true, note: noteRecord(note) });
  } catch {
    return fail(res, 500, 'send_failed');
  }
});

app.post('/reminder-create', async (req, res) => {
  const body = validateReminderCreateBody(req.body);
  if (!body.ok) {
    return fail(res, 400, 'invalid_request');
  }
  if (!isConnected()) {
    return fail(res, 503, 'not_connected');
  }
  if (!sendBucket.take()) {
    return fail(res, 429, 'rate_limited');
  }
  const options = {
    title: body.title,
    startTime: Date.now() + body.startsInMinutes * 60_000,
    repeat: REMINDER_REPEATS[body.repeat],
  };
  if (body.emoji) {
    options.emoji = body.emoji;
  }
  try {
    const reminder = await api.createReminder(
      options,
      body.chatId,
      toZaloThreadType(body.threadType),
    );
    return res.json({
      success: true,
      reminder: reminderRecords([reminder])[0] || null,
    });
  } catch {
    return fail(res, 500, 'send_failed');
  }
});

app.post('/pin', async (req, res) => {
  const body = validatePinBody(req.body);
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
    await api.setPinnedConversations(
      body.pinned,
      body.chatId,
      toZaloThreadType(body.threadType),
    );
    return res.json({ success: true, pinned: body.pinned });
  } catch {
    return fail(res, 500, 'send_failed');
  }
});

app.post('/mute', async (req, res) => {
  const body = validateMuteBody(req.body);
  if (!body.ok) {
    return fail(res, 400, 'invalid_request');
  }
  if (!isConnected()) {
    return fail(res, 503, 'not_connected');
  }
  if (!sendBucket.take()) {
    return fail(res, 429, 'rate_limited');
  }
  const muted = body.action === 'mute';
  const params = {
    action: muted ? MuteAction.MUTE : MuteAction.UNMUTE,
  };
  if (muted) {
    params.duration = MUTE_DURATIONS[body.duration];
  }
  try {
    await api.setMute(params, body.chatId, toZaloThreadType(body.threadType));
    return res.json({
      success: true,
      muted,
      duration: muted ? body.duration : null,
    });
  } catch {
    return fail(res, 500, 'send_failed');
  }
});

app.get('/labels', async (req, res) => {
  if (!isConnected()) {
    return fail(res, 503, 'not_connected');
  }
  if (!readBucket.take()) {
    return fail(res, 429, 'rate_limited');
  }
  try {
    const current = await api.getLabels();
    return res.json({
      labels: labelEntries(current?.labelData),
      version: Number(current?.version) || 0,
    });
  } catch (error) {
    return fromInfoError(res, error);
  }
});

app.post('/labels', async (req, res) => {
  const body = validateLabelsBody(req.body);
  if (!body.ok) {
    return fail(res, 400, 'invalid_request');
  }
  if (body.action === 'list') {
    return fail(res, 400, 'invalid_request');
  }
  if (!isConnected()) {
    return fail(res, 503, 'not_connected');
  }
  if (!sendBucket.take()) {
    return fail(res, 429, 'rate_limited');
  }
  try {
    // The version is read again right before the write so a concurrent label
    // edit is reported instead of overwritten with stale data.
    const current = await api.getLabels();
    const applied = applyLabelChange(current?.labelData, {
      labelId: body.labelId,
      chatId: body.chatId,
      threadType: body.threadType,
      add: body.action === 'add',
    });
    if (!applied.ok) {
      return fail(
        res,
        applied.error === 'not_found' ? 404 : 502,
        applied.error === 'not_found' ? 'label_not_found' : 'upstream_failed',
      );
    }
    const updated = await api.updateLabels({
      labelData: applied.labelData,
      version: current?.version,
    });
    return res.json({
      success: true,
      label: applied.label,
      labels: labelEntries(updated?.labelData),
      version: Number(updated?.version) || 0,
    });
  } catch (error) {
    return fromInfoError(res, error);
  }
});

app.post('/unread-mark', async (req, res) => {
  const body = validateUnreadMarkBody(req.body);
  if (!body.ok) {
    return fail(res, 400, 'invalid_request');
  }
  if (!isConnected()) {
    return fail(res, 503, 'not_connected');
  }
  if (!sendBucket.take()) {
    return fail(res, 429, 'rate_limited');
  }
  const threadType = toZaloThreadType(body.threadType);
  try {
    if (body.unread) {
      await api.addUnreadMark(body.chatId, threadType);
    } else {
      await api.removeUnreadMark(body.chatId, threadType);
    }
    return res.json({ success: true, unread: body.unread });
  } catch {
    return fail(res, 500, 'send_failed');
  }
});

// Read one group through the info gate; the admin endpoints merge or list
// against fresh data because a previous write here may have changed it.
async function loadGroupInfo(groupId) {
  const info = await infoGate.run(
    groupInfoCacheKey([groupId]),
    () => api.getGroupInfo([groupId]),
    { ttlMs: GROUP_INFO_CACHE_TTL_MS, force: true },
  );
  const group = info?.gridInfoMap?.[groupId] || null;
  if (group) {
    rememberGroupName(groupNames, groupId, String(group.name || ''));
  }
  return group;
}

app.post('/group-members', async (req, res) => {
  const body = validateGroupMembersBody(req.body);
  if (!body.ok) {
    return fail(res, 400, 'invalid_request');
  }
  if (!isConnected()) {
    return fail(res, 503, 'not_connected');
  }
  if (body.action === 'list') {
    if (!readBucket.take()) {
      return fail(res, 429, 'rate_limited');
    }
    try {
      const group = await loadGroupInfo(body.groupId);
      if (!group) {
        return fail(res, 404, 'group_not_found');
      }
      const memberIds = Array.isArray(group.memberIds)
        ? group.memberIds.slice(0, body.memberLimit)
        : [];
      let profiles = null;
      if (memberIds.length) {
        profiles = await infoGate.run(
          `groupmembers:${body.groupId}:${memberIds.join(',')}`,
          () => api.getGroupMembersInfo(memberIds),
          { ttlMs: GROUP_INFO_CACHE_TTL_MS },
        );
      }
      return res.json(
        groupMembersRecord(group, profiles?.profiles, {
          memberLimit: body.memberLimit,
        }),
      );
    } catch (error) {
      return fromInfoError(res, error);
    }
  }
  if (!sendBucket.take()) {
    return fail(res, 429, 'rate_limited');
  }
  try {
    const response =
      body.action === 'add'
        ? await api.addUserToGroup(body.memberIds, body.groupId)
        : await api.removeUserFromGroup(body.memberIds, body.groupId);
    return res.json({
      success: true,
      action: body.action,
      ...memberChangeRecord(body.memberIds, response?.errorMembers),
    });
  } catch {
    return fail(res, 500, 'send_failed');
  }
});

app.post('/group-settings', async (req, res) => {
  const body = validateGroupSettingsBody(req.body);
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
    if (body.action === 'rename') {
      const response = await api.changeGroupName(body.name, body.groupId);
      const status = Number(response?.status);
      rememberGroupName(groupNames, body.groupId, body.name);
      return res.json({
        success: true,
        action: body.action,
        name: body.name,
        status: Number.isFinite(status) ? status : null,
        renamed: !Number.isFinite(status) || status === 0,
      });
    }
    // zca-js writes every setting key, so merge over the current values. The
    // merge is one upstream read, so it takes a read credit as well.
    if (!readBucket.take()) {
      return fail(res, 429, 'rate_limited');
    }
    const group = await loadGroupInfo(body.groupId);
    if (!group) {
      return fail(res, 404, 'group_not_found');
    }
    const options = groupSettingsOptions(group.setting, body.settings);
    await api.updateGroupSettings(options, body.groupId);
    return res.json({
      success: true,
      action: body.action,
      settings: groupSettingsRecord(options),
    });
  } catch (error) {
    return fromInfoError(res, error);
  }
});

app.post('/group-roles', async (req, res) => {
  const body = validateGroupRolesBody(req.body);
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
    if (body.action === 'add_deputy') {
      await api.addGroupDeputy(body.memberIds, body.groupId);
    } else {
      await api.removeGroupDeputy(body.memberIds, body.groupId);
    }
    return res.json({
      success: true,
      action: body.action,
      ...memberChangeRecord(body.memberIds, null),
    });
  } catch {
    return fail(res, 500, 'send_failed');
  }
});

app.post('/group-pending', async (req, res) => {
  const body = validateGroupPendingBody(req.body);
  if (!body.ok) {
    return fail(res, 400, 'invalid_request');
  }
  if (!isConnected()) {
    return fail(res, 503, 'not_connected');
  }
  if (body.action === 'list') {
    if (!readBucket.take()) {
      return fail(res, 429, 'rate_limited');
    }
    try {
      const response = await api.getPendingGroupMembers(body.groupId);
      const users = Array.isArray(response?.users) ? response.users : [];
      const pending = pendingMemberRecords(users, { limit: body.limit });
      return res.json({
        pending,
        truncated: users.length > pending.length,
      });
    } catch (error) {
      return fromInfoError(res, error);
    }
  }
  if (!sendBucket.take()) {
    return fail(res, 429, 'rate_limited');
  }
  try {
    const response = await api.reviewPendingMemberRequest(
      { members: body.memberIds, isApprove: body.action === 'approve' },
      body.groupId,
    );
    return res.json({
      success: true,
      action: body.action,
      results: reviewResults(body.memberIds, response),
    });
  } catch {
    return fail(res, 500, 'send_failed');
  }
});

app.post('/group-link', async (req, res) => {
  const body = validateGroupLinkBody(req.body);
  if (!body.ok) {
    return fail(res, 400, 'invalid_request');
  }
  if (!isConnected()) {
    return fail(res, 503, 'not_connected');
  }
  const bucket = body.action === 'get' ? readBucket : sendBucket;
  if (!bucket.take()) {
    return fail(res, 429, 'rate_limited');
  }
  try {
    let detail;
    if (body.action === 'enable') {
      detail = await api.enableGroupLink(body.groupId);
    } else if (body.action === 'disable') {
      await api.disableGroupLink(body.groupId);
      detail = { enabled: 0, link: '', expiration_date: 0 };
    } else {
      detail = await api.getGroupLinkDetail(body.groupId);
    }
    return res.json({
      success: true,
      action: body.action,
      enabled: Number(detail?.enabled) === 1,
      link: String(detail?.link || ''),
      expiration_date: Number(detail?.expiration_date) || 0,
    });
  } catch (error) {
    return fromInfoError(res, error);
  }
});

app.post('/create-group', async (req, res) => {
  const body = validateCreateGroupBody(req.body);
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
    const group = await api.createGroup({
      name: body.name,
      members: body.memberIds,
    });
    const groupId = normalizeMessageId(group?.groupId);
    if (groupId) {
      rememberGroupName(groupNames, groupId, body.name);
    }
    return res.json({
      success: true,
      group_id: groupId,
      name: body.name,
      ...memberChangeRecord(body.memberIds, group?.errorMembers),
    });
  } catch {
    return fail(res, 500, 'send_failed');
  }
});

app.post('/friend-requests', async (req, res) => {
  const body = validateFriendRequestsBody(req.body);
  if (!body.ok) {
    return fail(res, 400, 'invalid_request');
  }
  if (!isConnected()) {
    return fail(res, 503, 'not_connected');
  }
  const read = body.action === 'list_sent' || body.action === 'list_received';
  const bucket = read ? readBucket : sendBucket;
  if (!bucket.take()) {
    return fail(res, 429, 'rate_limited');
  }
  try {
    if (body.action === 'list_sent') {
      const sent = await api.getSentFriendRequest();
      return res.json({
        success: true,
        action: body.action,
        sent: sentFriendRequestRecords(sent, { limit: body.limit }),
      });
    }
    if (body.action === 'list_received') {
      // `getFriendRecommendations` mixes suggestions with received requests;
      // only recommType 2 is an actual incoming request.
      const recommendations = await api.getFriendRecommendations();
      return res.json({
        success: true,
        action: body.action,
        received: receivedFriendRequestRecords(recommendations, {
          limit: body.limit,
        }),
      });
    }
    if (body.action === 'accept') {
      await api.acceptFriendRequest(body.userId);
    } else if (body.action === 'reject') {
      await api.rejectFriendRequest(body.userId);
    } else if (body.action === 'undo') {
      await api.undoFriendRequest(body.userId);
    } else {
      await api.sendFriendRequest(body.message, body.userId);
    }
    return res.json({
      success: true,
      action: body.action,
      user_id: body.userId,
    });
  } catch (error) {
    return fromInfoError(res, error);
  }
});

app.post('/friend-alias', async (req, res) => {
  const body = validateFriendAliasBody(req.body);
  if (!body.ok) {
    return fail(res, 400, 'invalid_request');
  }
  if (!isConnected()) {
    return fail(res, 503, 'not_connected');
  }
  const read = body.action === 'list';
  const bucket = read ? readBucket : sendBucket;
  if (!bucket.take()) {
    return fail(res, 429, 'rate_limited');
  }
  try {
    if (body.action === 'list') {
      const response = await api.getAliasList(body.count, body.page);
      return res.json({
        success: true,
        action: body.action,
        aliases: aliasRecords(response),
      });
    }
    if (body.action === 'set') {
      await api.changeFriendAlias(body.alias, body.friendId);
    } else {
      await api.removeFriendAlias(body.friendId);
    }
    return res.json({
      success: true,
      action: body.action,
      friend_id: body.friendId,
      alias: body.action === 'set' ? body.alias : '',
    });
  } catch (error) {
    return fromInfoError(res, error);
  }
});

app.post('/quick-reply', async (req, res) => {
  const body = validateQuickReplyBody(req.body);
  if (!body.ok) {
    return fail(res, 400, 'invalid_request');
  }
  if (!isConnected()) {
    return fail(res, 503, 'not_connected');
  }
  const read = body.action === 'list';
  const bucket = read ? readBucket : sendBucket;
  if (!bucket.take()) {
    return fail(res, 429, 'rate_limited');
  }
  try {
    if (body.action === 'list') {
      const response = await api.getQuickMessageList();
      return res.json({
        success: true,
        action: body.action,
        quick_replies: quickReplyRecords(response?.items),
      });
    }
    if (body.action === 'remove') {
      await api.removeQuickMessage(body.itemId);
      return res.json({
        success: true,
        action: body.action,
        item_id: body.itemId,
        removed: true,
      });
    }
    const payload = { keyword: body.keyword, title: body.title };
    const response =
      body.action === 'add'
        ? await api.addQuickMessage(payload)
        : await api.updateQuickMessage(payload, body.itemId);
    return res.json({
      success: true,
      action: body.action,
      quick_reply: quickReplyRecords([response?.item])[0] || null,
    });
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
  // A quote or a mention belongs to one message; a split reply cannot carry it.
  if ((body.quoteMessageId || body.mentions.length > 0) && chunks.length !== 1) {
    return fail(res, 400, 'invalid_request');
  }
  let quote = null;
  if (body.quoteMessageId) {
    // The lookup is scoped to this thread, so a message id from any other chat
    // is never quotable.
    try {
      const reference = await findMessageRef(
        body.chatId,
        body.threadType,
        body.quoteMessageId,
      );
      quote = quotePayload(reference);
    } catch (error) {
      return fromInfoError(res, error);
    }
    if (!quote) {
      return fail(res, 404, 'not_found');
    }
  }
  let mentionList = [];
  if (body.mentions.length > 0) {
    try {
      const members = await groupMentionMembers(body.chatId, body.mentions);
      if (!members) {
        return fail(res, 400, 'invalid_request');
      }
      const built = mentionPayloads(chunks[0].msg, members);
      if (!built.ok) {
        return fail(res, 400, 'invalid_request');
      }
      mentionList = built.mentions;
    } catch (error) {
      return fromInfoError(res, error);
    }
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
      if (quote) {
        content.quote = quote;
      }
      if (mentionList.length) {
        content.mentions = mentionList;
      }
      const sent = await api.sendMessage(content, body.chatId, threadType);
      const messageId = sent?.message?.msgId;
      messageIds.push(messageId === undefined || messageId === null ? null : String(messageId));
    }
  } catch {
    return fail(res, 500, 'send_failed');
  }
  const timestamp = Date.now();
  chunks.forEach((chunk, index) => {
    const messageId = messageIds[index];
    if (!messageId) {
      return;
    }
    appendHistory({
      messageId,
      threadId: body.chatId,
      senderId: ownId,
      senderName: 'me',
      text: chunk.msg,
      timestamp,
      threadType: body.threadType,
      direction: 'out',
      type: 'text',
    });
  });
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
    // zca-js picks the upload path from the extension: images go to
    // photo_original, MP4 to the video path, everything else to a file.
    const sent = await api.sendMessage(
      { msg: body.caption, attachments: [filePath] },
      body.chatId,
      toZaloThreadType(body.threadType),
    );
    const messageId = sent?.message?.msgId ?? sent?.attachment?.[0]?.msgId;
    const normalized =
      messageId === undefined || messageId === null ? null : String(messageId);
    if (normalized) {
      appendHistory({
        messageId: normalized,
        threadId: body.chatId,
        senderId: ownId,
        senderName: 'me',
        text: body.caption,
        timestamp: Date.now(),
        threadType: body.threadType,
        direction: 'out',
        type: body.kind,
      });
    }
    return res.json({
      success: true,
      messageId: normalized,
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
    const messageId = sent?.msgId;
    const normalized =
      messageId === undefined || messageId === null ? null : String(messageId);
    if (normalized) {
      appendHistory({
        messageId: normalized,
        threadId: body.chatId,
        senderId: ownId,
        senderName: 'me',
        text: [body.message, body.url].filter(Boolean).join(' '),
        timestamp: Date.now(),
        threadType: body.threadType,
        direction: 'out',
        type: 'link',
      });
    }
    return res.json({
      success: true,
      messageId: normalized,
    });
  } catch {
    return fail(res, 500, 'send_failed');
  }
});

app.post('/send-sticker', async (req, res) => {
  const body = validateSendStickerBody(req.body);
  if (!body.ok) {
    return fail(res, 400, 'invalid_request');
  }
  if (!isConnected()) {
    return fail(res, 503, 'not_connected');
  }
  // The keyword search is an upstream read and the sticker is one send.
  if (!readBucket.take() || !sendBucket.take()) {
    return fail(res, 429, 'rate_limited');
  }
  try {
    const results = await api.searchSticker(body.keyword, MAX_STICKER_SEARCH_LIMIT);
    const sticker = Array.isArray(results) ? results[body.index] : null;
    if (!sticker) {
      return fail(res, 404, 'not_found');
    }
    const sent = await api.sendSticker(
      {
        id: Number(sticker.sticker_id),
        cateId: Number(sticker.cate_id),
        type: Number(sticker.type),
      },
      body.chatId,
      toZaloThreadType(body.threadType),
    );
    const messageId = normalizeMessageId(sent?.msgId);
    if (messageId) {
      appendHistory({
        messageId,
        threadId: body.chatId,
        senderId: ownId,
        senderName: 'me',
        text: '',
        timestamp: Date.now(),
        threadType: body.threadType,
        direction: 'out',
        type: 'sticker',
      });
    }
    return res.json({ success: true, messageId: messageId || null });
  } catch {
    return fail(res, 500, 'send_failed');
  }
});

app.post('/send-card', async (req, res) => {
  const body = validateSendCardBody(req.body);
  if (!body.ok) {
    return fail(res, 400, 'invalid_request');
  }
  if (!isConnected()) {
    return fail(res, 503, 'not_connected');
  }
  if (!sendBucket.take()) {
    return fail(res, 429, 'rate_limited');
  }
  // `sendCard` fetches the target's QR code first; checking it here reports an
  // id this account cannot see before anything is sent.
  try {
    const qr = await infoGate.run(
      `card:${body.userId}`,
      () => api.getQR(body.userId),
      { ttlMs: LIST_CACHE_TTL_MS },
    );
    if (!qr || !qr[body.userId]) {
      return fail(res, 404, 'recipient_not_found');
    }
  } catch (error) {
    return fromInfoError(res, error);
  }
  try {
    const options = { userId: body.userId };
    if (body.phoneNumber) {
      options.phoneNumber = body.phoneNumber;
    }
    const sent = await api.sendCard(
      options,
      body.chatId,
      toZaloThreadType(body.threadType),
    );
    const messageId = normalizeMessageId(sent?.msgId);
    if (messageId) {
      appendHistory({
        messageId,
        threadId: body.chatId,
        senderId: ownId,
        senderName: 'me',
        text: '',
        timestamp: Date.now(),
        threadType: body.threadType,
        direction: 'out',
        type: 'card',
      });
    }
    return res.json({ success: true, messageId: messageId || null });
  } catch {
    return fail(res, 500, 'send_failed');
  }
});

app.post('/forward', async (req, res) => {
  const body = validateForwardBody(req.body);
  if (!body.ok) {
    return fail(res, 400, 'invalid_request');
  }
  if (!isConnected()) {
    return fail(res, 503, 'not_connected');
  }
  const stored = storedMessage(body.sourceChatId, body.messageId);
  if (!stored) {
    return fail(res, 404, 'not_found');
  }
  if (!stored.text.trim()) {
    // zca-js forwards a text payload; a media-only message cannot be forwarded.
    return fail(res, 400, 'unsupported');
  }
  if (!sendBucket.take()) {
    return fail(res, 429, 'rate_limited');
  }
  try {
    const sent = await api.forwardMessage(
      {
        message: stored.text,
        reference: {
          id: stored.messageId,
          ts: stored.ts,
          logSrcType: 0,
          fwLvl: 1,
        },
      },
      [body.chatId],
      toZaloThreadType(body.threadType),
    );
    const forwarded = Array.isArray(sent?.success) ? sent.success[0] : null;
    const messageId = normalizeMessageId(forwarded?.msgId);
    if (!messageId) {
      return fail(res, 500, 'send_failed');
    }
    appendHistory({
      messageId,
      threadId: body.chatId,
      senderId: ownId,
      senderName: 'me',
      text: stored.text,
      timestamp: Date.now(),
      threadType: body.threadType,
      direction: 'out',
      type: 'forward',
    });
    return res.json({ success: true, messageId });
  } catch {
    return fail(res, 500, 'send_failed');
  }
});

app.post('/undo', async (req, res) => {
  const body = validateUndoBody(req.body);
  if (!body.ok) {
    return fail(res, 400, 'invalid_request');
  }
  if (!isConnected()) {
    return fail(res, 503, 'not_connected');
  }
  const stored = storedMessage(body.chatId, body.messageId);
  if (!stored) {
    return fail(res, 404, 'not_found');
  }
  if (stored.direction !== 'out') {
    return fail(res, 403, 'not_own_message');
  }
  if (undoExpired(stored.ts)) {
    return fail(res, 400, 'too_old');
  }
  let reference;
  try {
    reference = await findMessageRef(body.chatId, body.threadType, body.messageId);
  } catch (error) {
    return fromInfoError(res, error);
  }
  if (!reference) {
    return fail(res, 404, 'not_found');
  }
  if (!sendBucket.take()) {
    return fail(res, 429, 'rate_limited');
  }
  try {
    await api.undo(
      { msgId: body.messageId, cliMsgId: reference.cliMsgId },
      body.chatId,
      toZaloThreadType(body.threadType),
    );
    return res.json({ success: true });
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
  if (historyTrimTimer) {
    clearInterval(historyTrimTimer);
    historyTrimTimer = null;
  }
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
  startHistoryTrim();
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
