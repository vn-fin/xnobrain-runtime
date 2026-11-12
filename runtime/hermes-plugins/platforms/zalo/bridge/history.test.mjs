import test from 'node:test';
import assert from 'node:assert/strict';

import {
  HISTORY_MAX_AGE_MS,
  historyEntry,
  readHistoryRecords,
  selectHistoryRecords,
  trimHistoryLines,
} from './lib.js';

test('historyEntry normalizes the stored record shape', () => {
  assert.deepEqual(
    historyEntry({
      messageId: 12,
      threadId: ' 345 ',
      senderId: 678,
      senderName: 'Nam',
      text: 'hello',
      timestamp: 1_700_000_000_000,
      recovered: true,
    }),
    {
      ts: 1_700_000_000_000,
      messageId: '12',
      threadId: '345',
      threadType: 'user',
      direction: 'in',
      type: 'text',
      senderId: '678',
      senderName: 'Nam',
      text: 'hello',
      recovered: true,
    },
  );
});

test('historyEntry keeps outbound group records', () => {
  const record = historyEntry({
    messageId: '9',
    threadId: '345',
    senderName: 'me',
    text: 'report',
    threadType: 'group',
    direction: 'out',
    type: 'image',
  });
  assert.equal(record.threadType, 'group');
  assert.equal(record.direction, 'out');
  assert.equal(record.type, 'image');
});

test('historyEntry defaults missing fields safely', () => {
  const record = historyEntry({ messageId: '1', threadId: '2', senderId: '3', text: 'hi' });
  assert.equal(record.recovered, false);
  assert.equal(record.senderName, '');
  assert.equal(record.threadType, 'user');
  assert.equal(record.direction, 'in');
  assert.ok(Number.isFinite(record.ts));
});

test('trimHistoryLines keeps recent records and drops expired or malformed lines', () => {
  const now = 1_700_000_000_000;
  const recent = JSON.stringify({ ts: now - 1_000, messageId: '1' });
  const edge = JSON.stringify({ ts: now - HISTORY_MAX_AGE_MS, messageId: '2' });
  const expired = JSON.stringify({ ts: now - HISTORY_MAX_AGE_MS - 1, messageId: '3' });

  assert.deepEqual(
    trimHistoryLines(
      [recent, '', 'not-json', expired, edge, JSON.stringify({ messageId: '4' })],
      { now },
    ),
    [recent, edge],
  );
});

test('trimHistoryLines accepts a custom window', () => {
  const now = 10_000;
  const keep = JSON.stringify({ ts: 9_500, messageId: '1' });
  const drop = JSON.stringify({ ts: 8_000, messageId: '2' });
  assert.deepEqual(trimHistoryLines([keep, drop], { now, maxAgeMs: 1_000 }), [keep]);
});

test('trimHistoryLines tolerates non-array input', () => {
  assert.deepEqual(trimHistoryLines(null), []);
  assert.deepEqual(trimHistoryLines(undefined), []);
});

test('readHistoryRecords parses stored lines and drops expired ones', () => {
  const now = 1_700_000_000_000;
  const records = readHistoryRecords(
    [
      JSON.stringify({
        ts: now - 1_000,
        messageId: '7',
        threadId: '42',
        threadType: 'group',
        direction: 'out',
        type: 'image',
        senderName: 'me',
        text: 'photo',
      }),
      JSON.stringify({ ts: now - HISTORY_MAX_AGE_MS - 1, messageId: '6' }),
      'not-json',
      JSON.stringify({ ts: now, messageId: '8', text: 'hi' }),
    ],
    { now },
  );

  assert.deepEqual(
    records.map((record) => record.messageId),
    ['7', '8'],
  );
  assert.equal(records[0].threadType, 'group');
  assert.equal(records[0].direction, 'out');
  assert.equal(records[1].threadType, 'user');
});

test('selectHistoryRecords filters by thread, query and since', () => {
  const records = [
    { threadId: '1', messageId: 'a', text: 'Report ready', ts: 100 },
    { threadId: '2', messageId: 'b', text: 'unrelated', ts: 200 },
    { threadId: '1', messageId: 'c', text: 'report follow-up', ts: 300 },
    { threadId: '1', messageId: 'd', text: 'old report', ts: 50 },
  ];

  const filtered = selectHistoryRecords(records, {
    threadIds: ['1'],
    query: 'REPORT',
    since: 60,
    limit: 10,
  });

  assert.deepEqual(
    filtered.records.map((record) => record.messageId),
    ['a', 'c'],
  );
  assert.equal(filtered.matched, 2);
});

test('selectHistoryRecords keeps the newest entries and clips text', () => {
  const records = [
    { threadId: '1', messageId: 'a', text: 'a'.repeat(10), ts: 1 },
    { threadId: '1', messageId: 'b', text: 'b'.repeat(10), ts: 2 },
    { threadId: '1', messageId: 'c', text: 'c'.repeat(10), ts: 3 },
  ];

  const result = selectHistoryRecords(records, { limit: 2, textLimit: 4 });

  assert.deepEqual(
    result.records.map((record) => record.messageId),
    ['b', 'c'],
  );
  assert.equal(result.matched, 3);
  assert.equal(result.records[1].text, 'cccc');
});
