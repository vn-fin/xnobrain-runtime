import test from 'node:test';
import assert from 'node:assert/strict';

import { HISTORY_MAX_AGE_MS, historyEntry, trimHistoryLines } from './lib.js';

test('historyEntry normalizes the inbound record shape', () => {
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
      senderId: '678',
      senderName: 'Nam',
      text: 'hello',
      recovered: true,
    },
  );
});

test('historyEntry defaults missing fields safely', () => {
  const record = historyEntry({ messageId: '1', threadId: '2', senderId: '3', text: 'hi' });
  assert.equal(record.recovered, false);
  assert.equal(record.senderName, '');
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
