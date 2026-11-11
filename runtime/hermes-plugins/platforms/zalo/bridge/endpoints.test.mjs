import test from 'node:test';
import assert from 'node:assert/strict';

import {
  MAX_MEDIA_BYTES,
  MAX_MESSAGE_LENGTH,
  READ_RATE_LIMIT_PER_MINUTE,
  REACTION_NAMES,
  SEND_RATE_LIMIT_PER_MINUTE,
  chunkList,
  chunkText,
  constantTimeEqual,
  createTokenBucket,
  createTtlCache,
  detectImageType,
  inboundQueueItem,
  mediaFromContent,
  mentionsSelf,
  messageText,
  parseBearerToken,
  quotesSelf,
  receiptFields,
  safeImageFileName,
  validateReactBody,
  validateSeenBody,
  validateSendBody,
  validateSendLinkBody,
  validateSendMediaBody,
  validateSendMessageBody,
  validateThreadTarget,
  validateTypingBody,
} from './lib.js';

const PNG_HEADER = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 0]);
const JPEG_HEADER = Buffer.from([0xff, 0xd8, 0xff, 0xe0, 0, 0]);
const GIF87_HEADER = Buffer.from('GIF87a', 'latin1');
const GIF89_HEADER = Buffer.from('GIF89a', 'latin1');
const WEBP_HEADER = Buffer.from('RIFF\0\0\0\0WEBP', 'latin1');

function pngPayload(extraBytes = 0) {
  return Buffer.concat([PNG_HEADER, Buffer.alloc(extraBytes, 0x01)]);
}

test('constantTimeEqual accepts only identical non-empty strings', () => {
  assert.equal(constantTimeEqual('token', 'token'), true);
  assert.equal(constantTimeEqual('token', 'tokeN'), false);
  assert.equal(constantTimeEqual('token', 'wrong-length'), false);
  assert.equal(constantTimeEqual('', ''), false);
  assert.equal(constantTimeEqual('token', ''), false);
  assert.equal(constantTimeEqual('token', undefined), false);
  assert.equal(constantTimeEqual(undefined, undefined), false);
  assert.equal(constantTimeEqual(1234, 1234), false);
});

test('parseBearerToken accepts a Bearer header and rejects everything else', () => {
  assert.equal(parseBearerToken('Bearer abc123'), 'abc123');
  assert.equal(parseBearerToken('bearer   abc123 '), 'abc123');
  assert.equal(parseBearerToken('Bearer abc123 extra'), '');
  assert.equal(parseBearerToken('Token abc123'), '');
  assert.equal(parseBearerToken('Bearer'), '');
  assert.equal(parseBearerToken(''), '');
  assert.equal(parseBearerToken(undefined), '');
});

test('validateThreadTarget accepts numeric ids and defaults the thread type', () => {
  assert.deepEqual(validateThreadTarget({ chatId: '123' }), {
    ok: true,
    chatId: '123',
    threadType: 'user',
  });
  assert.deepEqual(validateThreadTarget({ chatId: ' 42 ', threadType: 'group' }), {
    ok: true,
    chatId: '42',
    threadType: 'group',
  });
  assert.deepEqual(validateThreadTarget({ chatId: '1'.repeat(32) }), {
    ok: true,
    chatId: '1'.repeat(32),
    threadType: 'user',
  });
});

test('validateThreadTarget rejects malformed targets', () => {
  assert.equal(validateThreadTarget(undefined).ok, false);
  assert.equal(validateThreadTarget([]).ok, false);
  assert.equal(validateThreadTarget({}).ok, false);
  assert.equal(validateThreadTarget({ chatId: '' }).ok, false);
  assert.equal(validateThreadTarget({ chatId: '   ' }).ok, false);
  assert.equal(validateThreadTarget({ chatId: '12a' }).ok, false);
  assert.equal(validateThreadTarget({ chatId: '-12' }).ok, false);
  assert.equal(validateThreadTarget({ chatId: '1'.repeat(33) }).ok, false);
  assert.equal(validateThreadTarget({ chatId: 123 }).ok, false);
  assert.equal(validateThreadTarget({ chatId: '123', threadType: 'dm' }).ok, false);
  assert.equal(validateThreadTarget({ chatId: '123', threadType: 1 }).ok, false);
});

test('validateSendMessageBody accepts 1..20000 characters', () => {
  assert.deepEqual(validateSendMessageBody({ chatId: '1', message: 'hello' }), {
    ok: true,
    chatId: '1',
    threadType: 'user',
    message: 'hello',
  });
  assert.equal(
    validateSendMessageBody({ chatId: '1', message: 'a'.repeat(MAX_MESSAGE_LENGTH) }).ok,
    true,
  );
  assert.equal(
    validateSendMessageBody({ chatId: '1', message: 'a'.repeat(MAX_MESSAGE_LENGTH + 1) }).ok,
    false,
  );
  assert.equal(validateSendMessageBody({ chatId: '1', message: '' }).ok, false);
  assert.equal(validateSendMessageBody({ chatId: '1', message: '   ' }).ok, false);
  assert.equal(validateSendMessageBody({ chatId: '1', message: 42 }).ok, false);
  assert.equal(validateSendMessageBody({ chatId: '1' }).ok, false);
  assert.equal(validateSendMessageBody({ message: 'hello' }).ok, false);
});

test('validateSendBody keeps the historical result shape', () => {
  assert.deepEqual(validateSendBody({ chatId: '123', message: 'hello world' }), {
    ok: true,
    chatId: '123',
    message: 'hello world',
  });
  assert.equal(validateSendBody({ chatId: 'abc', message: 'hello' }).ok, false);
  assert.equal(validateSendBody({ chatId: 123, message: 'hello' }).ok, false);
  assert.equal(validateSendBody({ chatId: '123', message: '   ' }).ok, false);
});

test('validateSendMediaBody decodes an allowed image', () => {
  const result = validateSendMediaBody({
    chatId: '42',
    threadType: 'group',
    fileName: '../../Photo.PNG',
    contentBase64: pngPayload(16).toString('base64'),
    caption: 'look',
  });
  assert.equal(result.ok, true);
  assert.equal(result.chatId, '42');
  assert.equal(result.threadType, 'group');
  assert.equal(result.fileName, 'Photo.png');
  assert.equal(result.mimeType, 'image/png');
  assert.equal(result.caption, 'look');
  assert.ok(Buffer.isBuffer(result.buffer));
  assert.equal(result.buffer.length, PNG_HEADER.length + 16);
});

test('validateSendMediaBody defaults the caption to an empty string', () => {
  const result = validateSendMediaBody({
    chatId: '42',
    fileName: 'photo',
    contentBase64: JPEG_HEADER.toString('base64'),
  });
  assert.equal(result.ok, true);
  assert.equal(result.caption, '');
  assert.equal(result.fileName, 'photo.jpg');
  assert.equal(result.mimeType, 'image/jpeg');
});

test('validateSendMediaBody rejects malformed or non-image payloads', () => {
  const good = {
    chatId: '42',
    fileName: 'photo.png',
    contentBase64: pngPayload().toString('base64'),
  };
  assert.equal(validateSendMediaBody(undefined).ok, false);
  assert.equal(validateSendMediaBody({ ...good, chatId: 'abc' }).ok, false);
  assert.equal(validateSendMediaBody({ ...good, fileName: '' }).ok, false);
  assert.equal(validateSendMediaBody({ ...good, fileName: 42 }).ok, false);
  assert.equal(validateSendMediaBody({ ...good, contentBase64: '' }).ok, false);
  assert.equal(validateSendMediaBody({ ...good, contentBase64: '***' }).ok, false);
  assert.equal(validateSendMediaBody({ ...good, contentBase64: 42 }).ok, false);
  assert.equal(validateSendMediaBody({ ...good, caption: 42 }).ok, false);
  assert.equal(validateSendMediaBody({ ...good, caption: 'a'.repeat(MAX_MESSAGE_LENGTH + 1) }).ok, false);
  assert.equal(
    validateSendMediaBody({
      ...good,
      contentBase64: Buffer.from('plain text').toString('base64'),
    }).ok,
    false,
  );
});

test('validateSendMediaBody rejects images larger than 10 MiB', () => {
  const oversized = pngPayload(MAX_MEDIA_BYTES + 1);
  const result = validateSendMediaBody({
    chatId: '42',
    fileName: 'photo.png',
    contentBase64: oversized.toString('base64'),
  });
  assert.equal(result.ok, false);
});

test('validateSendLinkBody accepts http and https links', () => {
  assert.deepEqual(validateSendLinkBody({ chatId: '1', url: 'https://example.com/a' }), {
    ok: true,
    chatId: '1',
    threadType: 'user',
    url: 'https://example.com/a',
    message: '',
  });
  assert.deepEqual(
    validateSendLinkBody({
      chatId: '1',
      threadType: 'group',
      url: ' http://example.com ',
      message: 'see this',
    }),
    {
      ok: true,
      chatId: '1',
      threadType: 'group',
      url: 'http://example.com',
      message: 'see this',
    },
  );
});

test('validateSendLinkBody rejects non-http links and long payloads', () => {
  assert.equal(validateSendLinkBody({ chatId: '1', url: 'example.com' }).ok, false);
  assert.equal(validateSendLinkBody({ chatId: '1', url: 'ftp://example.com' }).ok, false);
  assert.equal(validateSendLinkBody({ chatId: '1', url: 'javascript:alert(1)' }).ok, false);
  assert.equal(validateSendLinkBody({ chatId: '1', url: '' }).ok, false);
  assert.equal(validateSendLinkBody({ chatId: '1', url: 42 }).ok, false);
  assert.equal(
    validateSendLinkBody({ chatId: '1', url: `https://example.com/${'a'.repeat(2000)}` }).ok,
    false,
  );
  assert.equal(
    validateSendLinkBody({ chatId: '1', url: 'https://example.com', message: 42 }).ok,
    false,
  );
  assert.equal(
    validateSendLinkBody({
      chatId: '1',
      url: 'https://example.com',
      message: 'a'.repeat(MAX_MESSAGE_LENGTH + 1),
    }).ok,
    false,
  );
});

test('validateReactBody accepts every supported reaction', () => {
  for (const reaction of REACTION_NAMES) {
    assert.deepEqual(
      validateReactBody({ chatId: '42', messageId: '99', reaction, threadType: 'group' }),
      {
        ok: true,
        chatId: '42',
        threadType: 'group',
        messageId: '99',
        reaction,
      },
    );
  }
});

test('validateReactBody rejects unknown reactions and malformed ids', () => {
  assert.equal(
    validateReactBody({ chatId: '42', messageId: '99', reaction: 'LOVE' }).ok,
    false,
  );
  assert.equal(
    validateReactBody({ chatId: '42', messageId: '99', reaction: 'heart' }).ok,
    false,
  );
  assert.equal(
    validateReactBody({ chatId: '42', messageId: 'abc', reaction: 'HEART' }).ok,
    false,
  );
  assert.equal(
    validateReactBody({ chatId: '42', messageId: 99, reaction: 'HEART' }).ok,
    false,
  );
  assert.equal(
    validateReactBody({ chatId: '42', reaction: 'HEART' }).ok,
    false,
  );
  assert.equal(
    validateReactBody({ chatId: '42', messageId: '99', reaction: 'HEART', threadType: 'dm' }).ok,
    false,
  );
});

test('validateTypingBody validates only the thread target', () => {
  assert.deepEqual(validateTypingBody({ chatId: '7', threadType: 'group' }), {
    ok: true,
    chatId: '7',
    threadType: 'group',
  });
  assert.equal(validateTypingBody({ chatId: '7x' }).ok, false);
  assert.equal(validateTypingBody({}).ok, false);
});

test('validateSeenBody requires one numeric message id', () => {
  assert.deepEqual(validateSeenBody({ messageId: ' 123 ' }), {
    ok: true,
    messageId: '123',
  });
  assert.equal(validateSeenBody({}).ok, false);
  assert.equal(validateSeenBody(undefined).ok, false);
  assert.equal(validateSeenBody({ messageId: 'abc' }).ok, false);
  assert.equal(validateSeenBody({ messageId: 123 }).ok, false);
  assert.equal(validateSeenBody({ messageId: '1'.repeat(33) }).ok, false);
});

test('token bucket enforces its capacity and refills over time', () => {
  let clock = 0;
  const bucket = createTokenBucket({ capacity: 3, perMinute: 60, now: () => clock });

  assert.equal(bucket.take(), true);
  assert.equal(bucket.take(), true);
  assert.equal(bucket.take(), true);
  assert.equal(bucket.take(), false);

  clock += 1_000;
  assert.equal(bucket.take(), true);
  clock += 500;
  assert.equal(bucket.take(), false);
  clock += 500;
  assert.equal(bucket.take(), true);
});

test('token bucket supports multi-credit sends and never exceeds capacity', () => {
  let clock = 0;
  const bucket = createTokenBucket({ capacity: 4, perMinute: 120, now: () => clock });

  assert.equal(bucket.take(2), true);
  assert.equal(bucket.take(3), false);
  assert.equal(bucket.take(2), true);

  clock += 60_000;
  assert.equal(bucket.available(), 4);
  assert.equal(bucket.take(5), false);
});

test('read and send buckets start at their contract limits', () => {
  let clock = 0;
  const read = createTokenBucket({
    capacity: READ_RATE_LIMIT_PER_MINUTE,
    perMinute: READ_RATE_LIMIT_PER_MINUTE,
    now: () => clock,
  });
  const send = createTokenBucket({
    capacity: SEND_RATE_LIMIT_PER_MINUTE,
    perMinute: SEND_RATE_LIMIT_PER_MINUTE,
    now: () => clock,
  });

  for (let index = 0; index < READ_RATE_LIMIT_PER_MINUTE; index += 1) {
    assert.equal(read.take(), true);
  }
  assert.equal(read.take(), false);

  for (let index = 0; index < SEND_RATE_LIMIT_PER_MINUTE; index += 1) {
    assert.equal(send.take(), true);
  }
  assert.equal(send.take(), false);

  clock += 60_000;
  assert.equal(read.take(), true);
  assert.equal(send.take(), true);
});

test('chunkText returns no chunks for empty or non-string input', () => {
  assert.deepEqual(chunkText(''), []);
  assert.deepEqual(chunkText(null), []);
  assert.deepEqual(chunkText(42), []);
});

test('chunkText keeps an exactly 2000-character message in one chunk', () => {
  const text = 'a'.repeat(2000);
  assert.deepEqual(chunkText(text), [text]);
});

test('chunkText hard-cuts unbroken text without losing characters', () => {
  const text = 'x'.repeat(4500);
  const chunks = chunkText(text);
  assert.deepEqual(chunks.map((chunk) => chunk.length), [2000, 2000, 500]);
  assert.equal(chunks.join(''), text);
});

test('chunkText prefers newline and space boundaries', () => {
  const first = `${'a'.repeat(1990)}\n${'b'.repeat(50)}`;
  const newlineChunks = chunkText(first);
  assert.equal(newlineChunks.length, 2);
  assert.equal(newlineChunks[0].length, 1991);
  assert.ok(newlineChunks[0].endsWith('\n'));
  assert.equal(newlineChunks.join(''), first);

  const second = `${'a'.repeat(1995)} ${'b'.repeat(100)}`;
  const spaceChunks = chunkText(second);
  assert.equal(spaceChunks.length, 2);
  assert.equal(spaceChunks[0].length, 1996);
  assert.ok(spaceChunks[0].endsWith(' '));
  assert.equal(spaceChunks.join(''), second);
});

test('chunkText preserves order across many chunks', () => {
  const lines = [];
  for (let index = 0; index < 400; index += 1) {
    lines.push(`line ${index}`);
  }
  const text = lines.join('\n');
  const chunks = chunkText(text);
  assert.ok(chunks.length > 1);
  assert.ok(chunks.every((chunk) => chunk.length <= 2000));
  assert.equal(chunks.join(''), text);
});

test('chunkText honours a custom limit', () => {
  assert.deepEqual(chunkText('abcdefgh', 3), ['abc', 'def', 'gh']);
});

test('chunkList splits ids into ordered batches', () => {
  assert.deepEqual(chunkList([1, 2, 3, 4, 5], 2), [[1, 2], [3, 4], [5]]);
  assert.deepEqual(chunkList([], 2), []);
  assert.deepEqual(chunkList(null, 2), []);
});

test('detectImageType recognizes the four allowed image formats', () => {
  assert.deepEqual(detectImageType(PNG_HEADER), { mime: 'image/png', extension: 'png' });
  assert.deepEqual(detectImageType(JPEG_HEADER), { mime: 'image/jpeg', extension: 'jpg' });
  assert.deepEqual(detectImageType(GIF87_HEADER), { mime: 'image/gif', extension: 'gif' });
  assert.deepEqual(detectImageType(GIF89_HEADER), { mime: 'image/gif', extension: 'gif' });
  assert.deepEqual(detectImageType(WEBP_HEADER), { mime: 'image/webp', extension: 'webp' });
});

test('detectImageType rejects non-images and truncated buffers', () => {
  assert.equal(detectImageType(Buffer.from('plain text')), null);
  assert.equal(detectImageType(Buffer.from([0xff, 0xd8])), null);
  assert.equal(detectImageType(Buffer.from('GIF88a', 'latin1')), null);
  assert.equal(detectImageType(Buffer.from('RIFFxxxxWAVE', 'latin1')), null);
  assert.equal(detectImageType(Buffer.alloc(0)), null);
  assert.equal(detectImageType(null), null);
});

test('safeImageFileName strips directories and forces the detected extension', () => {
  assert.equal(safeImageFileName('../../etc/passwd', 'png'), 'passwd.png');
  assert.equal(safeImageFileName('holiday.JPEG', 'jpg'), 'holiday.jpg');
  assert.equal(safeImageFileName('archive.tar.gz', 'webp'), 'archive.tar.webp');
  assert.equal(safeImageFileName('   ', 'gif'), 'image.gif');
  assert.equal(safeImageFileName('..', 'png'), 'image.png');
  assert.equal(safeImageFileName(undefined, 'png'), 'image.png');
});

test('mentionsSelf matches the account id but not @all', () => {
  assert.equal(mentionsSelf([{ uid: '42', pos: 0, len: 3, type: 0 }], '42'), true);
  assert.equal(mentionsSelf([{ uid: 42 }], '42'), true);
  assert.equal(mentionsSelf([{ uid: '7' }], '42'), false);
  assert.equal(mentionsSelf([{ uid: '-1', type: 1 }], '42'), false);
  assert.equal(mentionsSelf([{ uid: '-1' }], '42'), false);
  assert.equal(mentionsSelf([{ uid: '42', type: 1 }], '42'), false);
  assert.equal(mentionsSelf([{ uid: '-1', type: 1 }, { uid: '42', type: 0 }], '42'), true);
  assert.equal(mentionsSelf([], '42'), false);
  assert.equal(mentionsSelf(null, '42'), false);
  assert.equal(mentionsSelf([{ uid: '42' }], ''), false);
  assert.equal(mentionsSelf([null, 'bad', { uid: '42' }], '42'), true);
});

test('quotesSelf matches the quote owner only', () => {
  assert.equal(quotesSelf({ ownerId: '42', msg: 'hi' }, '42'), true);
  assert.equal(quotesSelf({ ownerId: 42 }, '42'), true);
  assert.equal(quotesSelf({ ownerId: '7' }, '42'), false);
  assert.equal(quotesSelf({}, '42'), false);
  assert.equal(quotesSelf(null, '42'), false);
  assert.equal(quotesSelf({ ownerId: '42' }, ''), false);
});

test('messageText extracts string and object content', () => {
  assert.equal(messageText('hello'), 'hello');
  assert.equal(messageText({ title: 'Title' }), 'Title');
  assert.equal(messageText({ description: 'Description' }), 'Description');
  assert.equal(messageText({ title: ' ', description: 'Description' }), 'Description');
  assert.equal(messageText({}), '');
  assert.equal(messageText(null), '');
  assert.equal(messageText(42), '');
});

test('mediaFromContent describes attachments without exposing URLs', () => {
  assert.deepEqual(
    mediaFromContent({
      type: 'chat.photo',
      title: 'photo.jpg',
      href: 'https://cdn.example/photo.jpg',
      params: '{"fileSize":"1234"}',
    }),
    [{ kind: 'image', name: 'photo.jpg', sizeBytes: 1234 }],
  );
  assert.deepEqual(mediaFromContent({ type: 'chat.video.msg', title: 'clip.mp4' }), [
    { kind: 'video', name: 'clip.mp4', sizeBytes: null },
  ]);
  assert.deepEqual(mediaFromContent({ type: 'share.file', title: 'report.pdf', params: '{}' }), [
    { kind: 'file', name: 'report.pdf', sizeBytes: null },
  ]);
  assert.deepEqual(mediaFromContent({ type: 'chat.voice', title: 'voice' }), [
    { kind: 'other', name: 'voice', sizeBytes: null },
  ]);
  assert.deepEqual(mediaFromContent({ type: 'unknown.type', title: 'x' }), []);
  assert.deepEqual(mediaFromContent('plain text'), []);
  assert.deepEqual(mediaFromContent(null), []);
});

test('receiptFields copies only the identity and timing fields', () => {
  assert.deepEqual(
    receiptFields({
      msgId: '123',
      cliMsgId: 'c1',
      uidFrom: '9',
      idTo: '8',
      msgType: 'webchat',
      st: 1,
      at: 2,
      cmd: 3,
      ts: '1700000000000',
      content: 'private text',
      dName: 'Nam',
      isSelf: false,
    }),
    {
      msgId: '123',
      cliMsgId: 'c1',
      uidFrom: '9',
      idTo: '8',
      msgType: 'webchat',
      st: 1,
      at: 2,
      cmd: 3,
      ts: '1700000000000',
    },
  );
});

test('receiptFields falls back to realMsgId and rejects missing input', () => {
  assert.deepEqual(receiptFields({ realMsgId: 'r1', cliMsgId: 'c1' }), {
    msgId: 'r1',
    cliMsgId: 'c1',
    uidFrom: undefined,
    idTo: undefined,
    msgType: undefined,
    st: undefined,
    at: undefined,
    cmd: undefined,
    ts: undefined,
  });
  assert.equal(receiptFields(null), null);
  assert.equal(receiptFields([]), null);
  assert.equal(receiptFields({}), null);
  assert.equal(receiptFields({ msgId: '' }), null);
  assert.equal(receiptFields({ msgId: null }), null);
});

test('inboundQueueItem shapes a group message', () => {
  assert.deepEqual(
    inboundQueueItem({
      messageId: 123,
      threadId: '456',
      senderId: 789,
      senderName: 'Nam',
      text: 'hello group',
      timestamp: 1_700_000_000_000,
      threadType: 'group',
      groupName: 'Team',
      mentionsSelf: true,
      quotesSelf: false,
      media: [
        { kind: 'image', name: 'a.jpg', sizeBytes: 10 },
        { kind: 'unexpected' },
        null,
      ],
    }),
    {
      messageId: '123',
      threadId: '456',
      senderId: '789',
      senderName: 'Nam',
      text: 'hello group',
      timestamp: 1_700_000_000_000,
      threadType: 'group',
      groupName: 'Team',
      mentionsSelf: true,
      quotesSelf: false,
      media: [
        { kind: 'image', name: 'a.jpg', sizeBytes: 10 },
        { kind: 'other', name: '', sizeBytes: null },
      ],
    },
  );
});

test('inboundQueueItem defaults optional fields', () => {
  const item = inboundQueueItem({
    messageId: '1',
    threadId: '2',
    senderId: '3',
    text: 'hi',
  });
  assert.equal(item.threadType, 'user');
  assert.equal(item.groupName, '');
  assert.equal(item.mentionsSelf, false);
  assert.equal(item.quotesSelf, false);
  assert.deepEqual(item.media, []);
  assert.ok(Number.isFinite(item.timestamp));
});

test('TTL cache expires entries and evicts the oldest', () => {
  let clock = 0;
  const cache = createTtlCache({ ttlMs: 1_000, maxEntries: 2, now: () => clock });

  cache.set('a', 1);
  cache.set('b', 2);
  assert.equal(cache.get('a'), 1);
  assert.equal(cache.has('b'), true);

  clock += 999;
  assert.equal(cache.get('a'), 1);
  clock += 1;
  assert.equal(cache.get('a'), undefined);
  assert.equal(cache.has('a'), false);

  cache.set('c', 3);
  cache.set('d', 4);
  cache.set('e', 5);
  assert.equal(cache.size, 2);
  assert.equal(cache.get('c'), undefined);
  assert.equal(cache.get('e'), 5);

  cache.clear();
  assert.equal(cache.size, 0);
});
