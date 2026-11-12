import test from 'node:test';
import assert from 'node:assert/strict';

import {
  MAX_LABEL_CONVERSATIONS,
  MAX_QR_EXPIRY_ATTEMPTS,
  MAX_QUEUE_SIZE,
  MAX_RELOGIN_ATTEMPTS,
  applyLabelChange,
  classifyCloseCode,
  conversationLabelToken,
  cookieListFromJar,
  isLoopback,
  isNewerMessageId,
  labelEntries,
  nextQrExpiryAttempt,
  noteRecord,
  pushToQueue,
  reloginDelayMs,
  rememberCursor,
  shouldQueueMessage,
  validateSendBody,
} from './lib.js';

test('pushToQueue keeps the newest 100 entries and drops the oldest', () => {
  const queue = [];
  for (let index = 0; index < MAX_QUEUE_SIZE + 5; index += 1) {
    pushToQueue(queue, { id: index });
  }

  assert.equal(queue.length, MAX_QUEUE_SIZE);
  assert.deepEqual(queue[0], { id: 5 });
  assert.deepEqual(queue[queue.length - 1], { id: MAX_QUEUE_SIZE + 4 });
});

test('pushToQueue accepts a custom cap', () => {
  const queue = [];
  pushToQueue(queue, 'a', 2);
  pushToQueue(queue, 'b', 2);
  pushToQueue(queue, 'c', 2);

  assert.deepEqual(queue, ['b', 'c']);
});

test('cursor dedupe ignores seen ids and ids not newer than the cursor', () => {
  const cursor = { thread1: '100' };
  const seenIds = new Set(['100', '101']);

  assert.equal(
    shouldQueueMessage({
      cursor,
      seenIds,
      threadId: 'thread1',
      messageId: '101',
      requireNewer: true,
    }),
    false,
  );
  assert.equal(
    shouldQueueMessage({
      cursor,
      seenIds,
      threadId: 'thread1',
      messageId: '102',
      requireNewer: true,
    }),
    true,
  );
  assert.equal(
    shouldQueueMessage({
      cursor,
      seenIds,
      threadId: 'thread1',
      messageId: '99',
      requireNewer: true,
    }),
    false,
  );
  // Live delivery only needs the seen-id dedupe.
  assert.equal(
    shouldQueueMessage({
      cursor,
      seenIds,
      threadId: 'thread1',
      messageId: '101',
    }),
    false,
  );
});

test('rememberCursor only advances forward and handles big ids', () => {
  const cursor = {};
  rememberCursor(cursor, 'thread1', '9007199254740993');
  assert.equal(cursor.thread1, '9007199254740993');

  rememberCursor(cursor, 'thread1', '9007199254740992');
  assert.equal(cursor.thread1, '9007199254740993');

  rememberCursor(cursor, 'thread1', '9007199254740994');
  assert.equal(cursor.thread1, '9007199254740994');
});

test('isNewerMessageId compares decimal ids numerically', () => {
  assert.equal(isNewerMessageId('10', '9'), true);
  assert.equal(isNewerMessageId('9', '10'), false);
  assert.equal(isNewerMessageId('anything', ''), true);
  assert.equal(isNewerMessageId('', '10'), false);
});

test('isLoopback accepts IPv4, IPv6 and mapped loopback only', () => {
  assert.equal(isLoopback('127.0.0.1'), true);
  assert.equal(isLoopback('::1'), true);
  assert.equal(isLoopback('::ffff:127.0.0.1'), true);
  assert.equal(isLoopback('127.0.0.2'), false);
  assert.equal(isLoopback('::ffff:192.168.1.10'), false);
  assert.equal(isLoopback('10.0.0.5'), false);
  assert.equal(isLoopback(undefined), false);
});

test('validateSendBody rejects missing or blank fields', () => {
  assert.equal(validateSendBody(undefined).ok, false);
  assert.equal(validateSendBody({ message: 'hello' }).ok, false);
  assert.equal(validateSendBody({ chatId: '123' }).ok, false);
  assert.equal(validateSendBody({ chatId: '   ', message: 'hello' }).ok, false);
  assert.equal(validateSendBody({ chatId: '123', message: '   ' }).ok, false);
  assert.equal(validateSendBody({ chatId: 123, message: 'hello' }).ok, false);
});

test('validateSendBody returns trimmed chat id and preserves message', () => {
  const result = validateSendBody({ chatId: ' 123 ', message: 'hello world' });
  assert.deepEqual(result, { ok: true, chatId: '123', message: 'hello world' });
});

test('classifyCloseCode marks duplicate-connection and kick as terminal', () => {
  assert.deepEqual(classifyCloseCode(3000), {
    terminal: true,
    code: 3000,
    reason: 'duplicate connection',
  });
  assert.deepEqual(classifyCloseCode('3003'), {
    terminal: true,
    code: 3003,
    reason: 'kicked',
  });
  assert.equal(classifyCloseCode(1006).terminal, false);
  assert.equal(classifyCloseCode(1011).terminal, false);
  assert.equal(classifyCloseCode(undefined).terminal, false);
});

test('reloginDelayMs grows linearly and caps at the attempt limit', () => {
  assert.equal(MAX_RELOGIN_ATTEMPTS, 5);
  assert.equal(reloginDelayMs(1), 60_000);
  assert.equal(reloginDelayMs(2), 120_000);
  assert.equal(reloginDelayMs(5), 300_000);
  assert.equal(reloginDelayMs(0), 60_000);
});

test('nextQrExpiryAttempt stops after three consecutive expired QRs', () => {
  assert.equal(MAX_QR_EXPIRY_ATTEMPTS, 3);
  assert.deepEqual(nextQrExpiryAttempt(0), { attempts: 1, retry: true });
  assert.deepEqual(nextQrExpiryAttempt(1), { attempts: 2, retry: true });
  assert.deepEqual(nextQrExpiryAttempt(2), { attempts: 3, retry: false });
  assert.deepEqual(nextQrExpiryAttempt('bad'), { attempts: 1, retry: true });
});

test('cookieListFromJar serializes a CookieJar for login()', () => {
  const serialized = [{ name: 'zalo', value: 'token', domain: 'chat.zalo.me' }];
  assert.deepEqual(cookieListFromJar({ toJSON: () => ({ cookies: serialized }) }), serialized);
  assert.deepEqual(cookieListFromJar(serialized), serialized);
  assert.deepEqual(cookieListFromJar({ cookies: serialized }), serialized);
  assert.equal(cookieListFromJar(null), null);
  assert.equal(cookieListFromJar({}), null);
});

test('labelEntries exposes display fields only', () => {
  const labels = labelEntries([
    {
      id: 3,
      text: 'Work',
      textKey: 'work',
      conversations: ['u11', 'g22'],
      color: '#fff',
      offset: 0,
      emoji: '',
      createTime: 1,
    },
    { id: 'bad', text: 'Skip' },
    null,
  ]);

  assert.deepEqual(labels, [
    { label_id: '3', name: 'Work', conversation_count: 2 },
  ]);
  assert.deepEqual(labelEntries(null), []);
});

test('conversationLabelToken prefixes the thread kind', () => {
  assert.equal(conversationLabelToken('11', 'user'), 'u11');
  assert.equal(conversationLabelToken('22', 'group'), 'g22');
});

test('applyLabelChange adds and removes one conversation only', () => {
  const labelData = [
    { id: 3, text: 'Work', conversations: ['u11'] },
    { id: 4, text: 'Family', conversations: ['u99'] },
  ];

  const added = applyLabelChange(labelData, {
    labelId: '3',
    chatId: '22',
    threadType: 'group',
    add: true,
  });
  assert.equal(added.ok, true);
  assert.deepEqual(added.labelData[0].conversations, ['u11', 'g22']);
  // Other labels are untouched.
  assert.deepEqual(added.labelData[1], labelData[1]);
  assert.deepEqual(added.label, { label_id: '3', name: 'Work', conversation_count: 2 });
  // The caller's array is not mutated.
  assert.deepEqual(labelData[0].conversations, ['u11']);

  const removed = applyLabelChange(added.labelData, {
    labelId: 3,
    chatId: '11',
    threadType: 'user',
    add: false,
  });
  assert.equal(removed.ok, true);
  assert.deepEqual(removed.labelData[0].conversations, ['g22']);

  const again = applyLabelChange(removed.labelData, {
    labelId: 3,
    chatId: '22',
    threadType: 'group',
    add: true,
  });
  assert.deepEqual(again.labelData[0].conversations, ['g22']);
});

test('applyLabelChange refuses an unknown label or malformed data', () => {
  assert.deepEqual(
    applyLabelChange([{ id: 3, conversations: [] }], {
      labelId: 4,
      chatId: '11',
      threadType: 'user',
      add: true,
    }),
    { ok: false, error: 'not_found' },
  );
  assert.deepEqual(
    applyLabelChange('nope', {
      labelId: 3,
      chatId: '11',
      threadType: 'user',
      add: true,
    }),
    { ok: false, error: 'invalid_response' },
  );
  assert.equal(
    applyLabelChange(
      [
        {
          id: 3,
          conversations: Array.from(
            { length: MAX_LABEL_CONVERSATIONS },
            (_, index) => `u${index + 100}`,
          ),
        },
      ],
      { labelId: 3, chatId: '11', threadType: 'user', add: true },
    ).ok,
    false,
  );
});

test('noteRecord shapes one created note', () => {
  assert.deepEqual(
    noteRecord({
      id: '7',
      creatorId: '77',
      createTime: 10,
      editTime: 20,
      params: { title: 'Plan' },
    }),
    {
      note_id: '7',
      title: 'Plan',
      creator_id: '77',
      created_time: 10,
      updated_time: 20,
    },
  );
});
