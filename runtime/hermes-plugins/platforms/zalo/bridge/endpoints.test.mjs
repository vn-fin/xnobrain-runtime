import test from 'node:test';
import assert from 'node:assert/strict';

import {
  FRIEND_ALIAS_ACTIONS,
  FRIEND_REQUEST_ACTIONS,
  GROUP_LINK_ACTIONS,
  GROUP_PENDING_ACTIONS,
  GROUP_ROLE_ACTIONS,
  GROUP_SETTING_KEYS,
  GROUP_SETTING_ACTIONS,
  MAX_ALIAS_PAGE_SIZE,
  MAX_FRIEND_ALIAS_LENGTH,
  MAX_FRIEND_REQUEST_MESSAGE_LENGTH,
  MAX_GROUP_MEMBER_CHANGE,
  MAX_GROUP_MEMBER_LIMIT,
  MAX_GROUP_NAME_LENGTH,
  MAX_GROUP_NAMES,
  MAX_PENDING_MEMBERS,
  MAX_QUICK_REPLY_TITLE_LENGTH,
  MAX_QUICK_REPLIES,
  MAX_MEDIA_BYTES,
  MAX_MENTIONS,
  MAX_MESSAGE_LENGTH,
  MAX_POLL_EXPIRY_HOURS,
  MAX_POLL_OPTIONS,
  MAX_POLL_OPTION_LENGTH,
  MAX_REMINDER_EMOJI_LENGTH,
  MAX_REMINDER_MINUTES,
  MAX_STICKER_SEARCH_LIMIT,
  QUICK_REPLY_ACTIONS,
  READ_RATE_LIMIT_PER_MINUTE,
  REACTION_NAMES,
  SEND_RATE_LIMIT_PER_MINUTE,
  UNDO_MAX_AGE_MS,
  aliasRecords,
  chunkList,
  chunkText,
  constantTimeEqual,
  conversationEntries,
  conversationIds,
  createInfoGate,
  createTokenBucket,
  createTtlCache,
  detectAudioType,
  detectImageType,
  fileExtension,
  fillGroupNames,
  groupHistoryRecords,
  groupInfoCacheKey,
  groupInfoRecord,
  groupMembersRecord,
  groupSettingsOptions,
  groupSettingsRecord,
  inboundQueueItem,
  isMp4,
  isRateLimitError,
  markdownToZalo,
  mediaFromContent,
  memberChangeRecord,
  mentionPayloads,
  mentionsSelf,
  messageRef,
  messageText,
  noteRecords,
  parseBearerToken,
  pendingMemberRecords,
  pollRecord,
  quickReplyRecords,
  quotePayload,
  quotesSelf,
  receivedFriendRequestRecords,
  receiptFields,
  rememberGroupName,
  reminderRecords,
  reviewResults,
  reviewStatusName,
  safeImageFileName,
  safeMediaFileName,
  sentFriendRequestRecords,
  splitStyledChunks,
  undoExpired,
  unreadRecords,
  userInfoRecord,
  validateBoardBody,
  validateCreateGroupBody,
  validateForwardBody,
  validateFriendAliasBody,
  validateFriendRequestsBody,
  validateGroupHistoryBody,
  validateGroupInfoBody,
  validateGroupLinkBody,
  validateGroupMembersBody,
  validateGroupPendingBody,
  validateGroupRolesBody,
  validateGroupSettingsBody,
  validateHistoryBody,
  validateLabelsBody,
  validateMuteBody,
  validateNoteCreateBody,
  validatePinBody,
  validatePollCreateBody,
  validatePollDetailBody,
  validateQuickReplyBody,
  validateReactBody,
  validateReminderCreateBody,
  validateSearchBody,
  validateSeenBody,
  validateSendBody,
  validateSendCardBody,
  validateSendLinkBody,
  validateSendMediaBody,
  validateSendMessageBody,
  validateSendStickerBody,
  validateThreadTarget,
  validateTypingBody,
  validateUndoBody,
  validateUnreadMarkBody,
  validateUserInfoBody,
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
    quoteMessageId: '',
    mentions: [],
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

function makeFakeClock() {
  let time = 0;
  return {
    now: () => time,
    get time() {
      return time;
    },
    advance(ms) {
      time += Math.max(0, Number(ms) || 0);
    },
    async sleep(ms) {
      time += Math.max(0, Number(ms) || 0);
    },
  };
}

const isRateLimitedCode = (error) => error?.code === 'rate_limited';

test('info gate serves a fresh TTL hit and refetches after expiry', async () => {
  const clock = makeFakeClock();
  const gate = createInfoGate({ minIntervalMs: 100, now: clock.now, sleep: clock.sleep });
  let calls = 0;
  const fetcher = async () => {
    calls += 1;
    return { calls };
  };

  const first = await gate.run('me', fetcher, { ttlMs: 1_000 });
  clock.advance(999);
  assert.deepEqual(await gate.run('me', fetcher, { ttlMs: 1_000 }), first);
  assert.equal(calls, 1);

  clock.advance(1);
  assert.deepEqual(await gate.run('me', fetcher, { ttlMs: 1_000 }), { calls: 2 });
  assert.equal(calls, 2);
});

test('info gate force bypasses a fresh cache entry', async () => {
  const clock = makeFakeClock();
  const gate = createInfoGate({ minIntervalMs: 0, now: clock.now, sleep: clock.sleep });
  let calls = 0;
  const fetcher = async () => {
    calls += 1;
    return calls;
  };

  assert.equal(await gate.run('me', fetcher, { ttlMs: 60_000 }), 1);
  assert.equal(await gate.run('me', fetcher, { ttlMs: 60_000, force: true }), 2);
  assert.equal(await gate.run('me', fetcher, { ttlMs: 60_000 }), 2);
  assert.equal(calls, 2);
});

test('info gate serializes calls with at least the minimum gap', async () => {
  const clock = makeFakeClock();
  const gate = createInfoGate({ minIntervalMs: 1_500, now: clock.now, sleep: clock.sleep });
  const calls = [];
  const fetcher = (key) => async () => {
    calls.push({ key, at: clock.time });
    return key;
  };

  const results = await Promise.all([
    gate.run('a', fetcher('a'), { ttlMs: 1_000 }),
    gate.run('b', fetcher('b'), { ttlMs: 1_000 }),
    gate.run('c', fetcher('c'), { ttlMs: 1_000 }),
  ]);

  assert.deepEqual(results, ['a', 'b', 'c']);
  assert.deepEqual(calls, [
    { key: 'a', at: 0 },
    { key: 'b', at: 1_500 },
    { key: 'c', at: 3_000 },
  ]);
});

test('info gate doubles the backoff and caps it', async () => {
  const clock = makeFakeClock();
  const gate = createInfoGate({
    minIntervalMs: 0,
    baseBackoffMs: 1_000,
    maxBackoffMs: 3_000,
    now: clock.now,
    sleep: clock.sleep,
  });
  let calls = 0;
  const failing = async () => {
    calls += 1;
    throw new Error('Too many requests');
  };

  // First failure: backoff is the base interval.
  await assert.rejects(gate.run('me', failing, { ttlMs: 1_000 }), isRateLimitedCode);
  assert.equal(calls, 1);

  // Inside the window the upstream is not called again.
  await assert.rejects(gate.run('me', failing, { ttlMs: 1_000 }), isRateLimitedCode);
  assert.equal(calls, 1);

  clock.advance(1_000);
  await assert.rejects(gate.run('me', failing, { ttlMs: 1_000 }), isRateLimitedCode);
  assert.equal(calls, 2);

  clock.advance(2_000);
  await assert.rejects(gate.run('me', failing, { ttlMs: 1_000 }), isRateLimitedCode);
  assert.equal(calls, 3);

  // 3s -> capped at 3s.
  clock.advance(3_000);
  await assert.rejects(gate.run('me', failing, { ttlMs: 1_000 }), isRateLimitedCode);
  assert.equal(calls, 4);

  clock.advance(2_999);
  await assert.rejects(gate.run('me', failing, { ttlMs: 1_000 }), isRateLimitedCode);
  assert.equal(calls, 4);
});

test('info gate serves stale data while throttled', async () => {
  const clock = makeFakeClock();
  const gate = createInfoGate({
    minIntervalMs: 0,
    baseBackoffMs: 5_000,
    now: clock.now,
    sleep: clock.sleep,
  });
  let calls = 0;
  const working = async () => {
    calls += 1;
    return ['friend'];
  };
  const throttled = async () => {
    calls += 1;
    throw new Error('429 Too Many Requests');
  };

  assert.deepEqual(await gate.run('friends', working, { ttlMs: 1_000 }), ['friend']);
  clock.advance(2_000);
  assert.deepEqual(await gate.run('friends', throttled, { ttlMs: 1_000 }), ['friend']);
  assert.equal(calls, 2);

  // Still inside the backoff window, still stale, still no upstream call.
  assert.deepEqual(await gate.run('friends', throttled, { ttlMs: 1_000 }), ['friend']);
  assert.equal(calls, 2);
});

test('info gate throws rate_limited when it has no cached value', async () => {
  const clock = makeFakeClock();
  const gate = createInfoGate({ minIntervalMs: 0, now: clock.now, sleep: clock.sleep });

  await assert.rejects(
    gate.run(
      'groups:list',
      async () => {
        throw { code: 429, message: 'abnormal request' };
      },
      { ttlMs: 1_000 },
    ),
    isRateLimitedCode,
  );
});

test('info gate resets the backoff after a success', async () => {
  const clock = makeFakeClock();
  const gate = createInfoGate({
    minIntervalMs: 0,
    baseBackoffMs: 1_000,
    maxBackoffMs: 10_000,
    now: clock.now,
    sleep: clock.sleep,
  });
  let attempts = 0;
  let mode = 'fail';
  const fetcher = async () => {
    attempts += 1;
    if (mode === 'fail') {
      throw new Error('rate limited');
    }
    return 'ok';
  };

  // Two throttling failures would normally push the backoff to 2s.
  await assert.rejects(gate.run('me', fetcher, { ttlMs: 60_000 }), isRateLimitedCode);
  clock.advance(1_000);
  await assert.rejects(gate.run('me', fetcher, { ttlMs: 60_000 }), isRateLimitedCode);
  assert.equal(attempts, 2);

  // A success clears the accumulated backoff.
  clock.advance(2_000);
  mode = 'ok';
  assert.equal(await gate.run('me', fetcher, { ttlMs: 60_000 }), 'ok');
  assert.equal(attempts, 3);

  // The next failure on an uncached key starts again from the base interval.
  mode = 'fail';
  await assert.rejects(gate.run('other', fetcher, { ttlMs: 0 }), isRateLimitedCode);
  assert.equal(attempts, 4);

  clock.advance(999);
  await assert.rejects(gate.run('other', fetcher, { ttlMs: 0 }), isRateLimitedCode);
  assert.equal(attempts, 4);

  clock.advance(1);
  await assert.rejects(gate.run('other', fetcher, { ttlMs: 0 }), isRateLimitedCode);
  assert.equal(attempts, 5);
});

test('info gate queue survives a rejected fetch', async () => {
  const clock = makeFakeClock();
  const gate = createInfoGate({ minIntervalMs: 1_000, now: clock.now, sleep: clock.sleep });
  let calls = 0;
  const failing = async () => {
    calls += 1;
    throw new Error('boom');
  };
  const working = async () => {
    calls += 1;
    return 'value';
  };

  const first = gate.run('bad', failing, { ttlMs: 1_000 });
  const second = gate.run('good', working, { ttlMs: 1_000 });

  await assert.rejects(first, /boom/);
  assert.equal(await second, 'value');
  assert.equal(calls, 2);
});

test('isRateLimitError recognizes throttling hints only', () => {
  assert.equal(isRateLimitError(new Error('Rate limit exceeded')), true);
  assert.equal(isRateLimitError(new Error('too many requests')), true);
  assert.equal(isRateLimitError({ code: 429 }), true);
  assert.equal(isRateLimitError(new Error('account abnormal')), true);
  assert.equal(isRateLimitError(new Error('spam detected')), true);
  assert.equal(isRateLimitError({ code: 'RATE_LIMITED' }), true);
  assert.equal(isRateLimitError(new Error('network down')), false);
  assert.equal(isRateLimitError(new Error('could not generate thumbnail')), false);
  assert.equal(isRateLimitError({ code: 500 }), false);
  assert.equal(isRateLimitError(null), false);
  assert.equal(isRateLimitError(undefined), false);
});

test('groupInfoCacheKey sorts and joins ids', () => {
  assert.equal(groupInfoCacheKey(['9', '2']), 'groupinfo:2,9');
  assert.equal(groupInfoCacheKey(['2']), 'groupinfo:2');
  assert.equal(groupInfoCacheKey([9, '9']), 'groupinfo:9,9');
  assert.equal(groupInfoCacheKey([]), 'groupinfo:');
});

test('rememberGroupName keeps the newest names and drops the oldest', () => {
  const names = new Map();
  for (let index = 0; index < MAX_GROUP_NAMES + 5; index += 1) {
    rememberGroupName(names, String(index), `G${index}`);
  }
  assert.equal(names.size, MAX_GROUP_NAMES);
  assert.equal(names.has('0'), false);
  assert.equal(names.has('4'), false);
  assert.equal(names.has('5'), true);
  assert.equal(names.get(String(MAX_GROUP_NAMES + 4)), `G${MAX_GROUP_NAMES + 4}`);
});

test('rememberGroupName ignores empty ids and names', () => {
  const names = new Map();
  rememberGroupName(names, '', 'Name');
  rememberGroupName(names, '1', '');
  assert.equal(names.size, 0);
});

test('fillGroupNames fills only empty names on group messages', () => {
  const names = new Map([['42', 'Team']]);
  const messages = [
    { threadId: '42', threadType: 'group', groupName: '' },
    { threadId: '42', threadType: 'group', groupName: 'Keep' },
    { threadId: '42', threadType: 'user', groupName: '' },
    { threadId: '7', threadType: 'group', groupName: '' },
  ];

  const result = fillGroupNames(messages, names);
  assert.equal(result[0].groupName, 'Team');
  assert.equal(result[1].groupName, 'Keep');
  assert.equal(result[2].groupName, '');
  assert.equal(result[3].groupName, '');
  assert.equal(fillGroupNames(null, names), null);
});

test('markdownToZalo styles bold, italic and strikethrough', () => {
  assert.deepEqual(markdownToZalo('**bold**'), {
    msg: 'bold',
    styles: [{ start: 0, len: 4, st: 'b' }],
  });
  assert.deepEqual(markdownToZalo('__bold__'), {
    msg: 'bold',
    styles: [{ start: 0, len: 4, st: 'b' }],
  });
  assert.deepEqual(markdownToZalo('*italic*'), {
    msg: 'italic',
    styles: [{ start: 0, len: 6, st: 'i' }],
  });
  assert.deepEqual(markdownToZalo('_italic_'), {
    msg: 'italic',
    styles: [{ start: 0, len: 6, st: 'i' }],
  });
  assert.deepEqual(markdownToZalo('~~gone~~'), {
    msg: 'gone',
    styles: [{ start: 0, len: 4, st: 's' }],
  });
});

test('markdownToZalo supports bold with nested italic', () => {
  assert.deepEqual(markdownToZalo('**bold _italic_ text**'), {
    msg: 'bold italic text',
    styles: [
      { start: 5, len: 6, st: 'i' },
      { start: 0, len: 16, st: 'b' },
    ],
  });
});

test('markdownToZalo converts headings and list bullets', () => {
  assert.deepEqual(markdownToZalo('# Title'), {
    msg: 'Title',
    styles: [{ start: 0, len: 5, st: 'b' }],
  });
  assert.deepEqual(markdownToZalo('###### Deep'), {
    msg: 'Deep',
    styles: [{ start: 0, len: 4, st: 'b' }],
  });
  assert.deepEqual(markdownToZalo('- one\n* two\n+ three'), {
    msg: '• one\n• two\n• three',
    styles: [],
  });
  assert.deepEqual(markdownToZalo('- **bold**'), {
    msg: '• bold',
    styles: [{ start: 2, len: 4, st: 'b' }],
  });
});

test('markdownToZalo strips inline code and leaves fenced blocks literal', () => {
  assert.deepEqual(markdownToZalo('run `npm test` now'), {
    msg: 'run npm test now',
    styles: [],
  });
  assert.deepEqual(markdownToZalo('`**not bold**`'), {
    msg: '**not bold**',
    styles: [],
  });
  assert.deepEqual(markdownToZalo('before\n```js\nconst a = **1**;\n```\nafter'), {
    msg: 'before\nconst a = **1**;\nafter',
    styles: [],
  });
});

test('markdownToZalo renders links as label and url', () => {
  assert.deepEqual(markdownToZalo('see [docs](https://example.com)'), {
    msg: 'see docs (https://example.com)',
    styles: [],
  });
  assert.deepEqual(markdownToZalo('[**bold**](https://example.com)'), {
    msg: 'bold (https://example.com)',
    styles: [{ start: 0, len: 4, st: 'b' }],
  });
});

test('markdownToZalo keeps snake_case literal and leaves unmatched markers', () => {
  assert.deepEqual(markdownToZalo('snake_case_value'), {
    msg: 'snake_case_value',
    styles: [],
  });
  assert.deepEqual(markdownToZalo('a ** b'), { msg: 'a ** b', styles: [] });
  assert.deepEqual(markdownToZalo('2 * 3'), { msg: '2 * 3', styles: [] });
  assert.deepEqual(markdownToZalo('hello **world'), { msg: 'hello **world', styles: [] });
  assert.deepEqual(markdownToZalo('#hashtag stays'), { msg: '#hashtag stays', styles: [] });
});

test('markdownToZalo styles underscore italics outside words', () => {
  assert.deepEqual(markdownToZalo('_private var_'), {
    msg: 'private var',
    styles: [{ start: 0, len: 11, st: 'i' }],
  });
});

test('markdownToZalo offsets count UTF-16 code units', () => {
  assert.deepEqual(markdownToZalo('**😀** ok'), {
    msg: '😀 ok',
    styles: [{ start: 0, len: 2, st: 'b' }],
  });
});

test('markdownToZalo handles Vietnamese text', () => {
  assert.deepEqual(markdownToZalo('Xin chào **bạn** nhé'), {
    msg: 'Xin chào bạn nhé',
    styles: [{ start: 9, len: 3, st: 'b' }],
  });
});

test('markdownToZalo never throws on odd input', () => {
  assert.deepEqual(markdownToZalo(''), { msg: '', styles: [] });
  assert.deepEqual(markdownToZalo(null), { msg: '', styles: [] });
  assert.deepEqual(markdownToZalo(42), { msg: '42', styles: [] });
  assert.deepEqual(markdownToZalo('```'), { msg: '', styles: [] });
});

test('splitStyledChunks splits a style across a chunk boundary', () => {
  const msg = 'a'.repeat(5) + 'b'.repeat(5);
  const chunks = splitStyledChunks(msg, [{ start: 3, len: 4, st: 'b' }], 5);

  assert.deepEqual(chunks, [
    { msg: 'aaaaa', styles: [{ start: 3, len: 2, st: 'b' }] },
    { msg: 'bbbbb', styles: [{ start: 0, len: 2, st: 'b' }] },
  ]);
  assert.equal(chunks.map((chunk) => chunk.msg).join(''), msg);
});

test('splitStyledChunks rebases offsets per chunk', () => {
  const text = `${'a'.repeat(4)}\n${'b'.repeat(4)}`;
  const chunks = splitStyledChunks(text, [{ start: 5, len: 4, st: 'i' }], 5);

  assert.equal(chunks.length, 2);
  assert.equal(chunks[0].msg, 'aaaa\n');
  assert.deepEqual(chunks[0].styles, []);
  assert.deepEqual(chunks[1], { msg: 'bbbb', styles: [{ start: 0, len: 4, st: 'i' }] });
});

test('splitStyledChunks preserves the full converted text', () => {
  const converted = markdownToZalo(`**${'x'.repeat(120)}**`);
  const chunks = splitStyledChunks(converted.msg, converted.styles, 50);

  assert.ok(chunks.length > 1);
  assert.equal(chunks.map((chunk) => chunk.msg).join(''), converted.msg);
  const covered = chunks.flatMap((chunk) => chunk.styles.map((style) => style.len));
  assert.equal(covered.reduce((sum, len) => sum + len, 0), 120);
});

test('splitStyledChunks returns no chunks for empty text', () => {
  assert.deepEqual(splitStyledChunks('', []), []);
  assert.deepEqual(splitStyledChunks(null, []), []);
});

test('validateHistoryBody accepts a direct-message read with defaults', () => {
  assert.deepEqual(validateHistoryBody({ chatId: ' 42 ' }), {
    ok: true,
    chatId: '42',
    limit: 50,
    sinceHours: null,
    backfill: true,
  });
  assert.deepEqual(validateHistoryBody({ chatId: '42', limit: 200, sinceHours: 24 }), {
    ok: true,
    chatId: '42',
    limit: 200,
    sinceHours: 24,
    backfill: true,
  });
});

test('validateHistoryBody rejects bad ids, limits and sizes', () => {
  for (const body of [
    null,
    {},
    { chatId: 'zalo:42' },
    { chatId: '42', limit: 0 },
    { chatId: '42', limit: 201 },
    { chatId: '42', limit: '50' },
    { chatId: '42', sinceHours: -1 },
    { chatId: '42', sinceHours: 9_000 },
    { chatId: '42', backfill: 'yes' },
  ]) {
    assert.deepEqual(validateHistoryBody(body), { ok: false, error: 'invalid_request' });
  }
});

test('validateGroupHistoryBody caps the count and requires a group id', () => {
  assert.deepEqual(validateGroupHistoryBody({ groupId: '123', limit: 80 }), {
    ok: true,
    groupId: '123',
    limit: 80,
  });
  assert.equal(validateGroupHistoryBody({ groupId: '' }).ok, false);
  assert.equal(validateGroupHistoryBody({ groupId: '123', limit: 500 }).ok, false);
});

test('validateSearchBody requires a query and validates thread scope', () => {
  assert.deepEqual(validateSearchBody({ query: ' report ' }), {
    ok: true,
    query: 'report',
    chatIds: null,
    limit: 50,
    sinceHours: null,
  });
  assert.deepEqual(validateSearchBody({ query: 'report', chatIds: ['1', 2], limit: 10 }), {
    ok: true,
    query: 'report',
    chatIds: ['1', '2'],
    limit: 10,
    sinceHours: null,
  });
  assert.equal(validateSearchBody({ query: '' }).ok, false);
  assert.equal(validateSearchBody({ query: 'x'.repeat(201) }).ok, false);
  assert.equal(validateSearchBody({ query: 'report', chatIds: ['nope'] }).ok, false);
  assert.equal(validateSearchBody({ query: 'report', chatIds: '1' }).ok, false);
});

test('validateGroupInfoBody bounds the member preview', () => {
  assert.deepEqual(validateGroupInfoBody({ groupId: '9' }), {
    ok: true,
    groupId: '9',
    memberLimit: 50,
  });
  assert.equal(validateGroupInfoBody({ groupId: '9', memberLimit: 0 }).ok, true);
  assert.equal(validateGroupInfoBody({ groupId: '9', memberLimit: 101 }).ok, false);
  assert.equal(validateGroupInfoBody({ groupId: '9', memberLimit: -1 }).ok, false);
});

test('validateUserInfoBody accepts exactly one lookup key', () => {
  assert.deepEqual(validateUserInfoBody({ userId: '42' }), {
    ok: true,
    field: 'userId',
    value: '42',
  });
  assert.deepEqual(validateUserInfoBody({ phone: '+84912345678' }), {
    ok: true,
    field: 'phone',
    value: '+84912345678',
  });
  assert.deepEqual(validateUserInfoBody({ username: 'nam.nguyen' }), {
    ok: true,
    field: 'username',
    value: 'nam.nguyen',
  });
  for (const body of [
    null,
    {},
    { userId: '42', phone: '+84912345678' },
    { userId: 'zalo' },
    { phone: 'abc' },
    { username: 'with space' },
    { userId: '42', extra: true },
  ]) {
    assert.equal(validateUserInfoBody(body).ok, false);
  }
});

test('validateBoardBody bounds paging and count', () => {
  assert.deepEqual(validateBoardBody({ groupId: '7' }), {
    ok: true,
    groupId: '7',
    page: 1,
    count: 20,
  });
  assert.equal(validateBoardBody({ groupId: '7', page: 2, count: 100 }).ok, true);
  assert.equal(validateBoardBody({ groupId: '7', page: 0 }).ok, false);
  assert.equal(validateBoardBody({ groupId: '7', count: 101 }).ok, false);
  assert.equal(validateBoardBody({}).ok, false);
});

test('validatePollDetailBody requires both a group and a poll id', () => {
  assert.deepEqual(validatePollDetailBody({ groupId: '7', pollId: 8 }), {
    ok: true,
    groupId: '7',
    pollId: '8',
  });
  assert.deepEqual(validatePollDetailBody({ groupId: '7', pollId: '8' }), {
    ok: true,
    groupId: '7',
    pollId: '8',
  });
  assert.equal(validatePollDetailBody({ groupId: '7' }).ok, false);
  assert.equal(validatePollDetailBody({ groupId: '7', pollId: 'x' }).ok, false);
});

test('validatePollCreateBody trims and bounds the question and options', () => {
  assert.deepEqual(
    validatePollCreateBody({
      groupId: '7',
      question: ' Lunch? ',
      options: [' Pizza ', 'Pho'],
      allowMultiChoices: true,
      expiresInHours: 24,
    }),
    {
      ok: true,
      groupId: '7',
      question: 'Lunch?',
      options: ['Pizza', 'Pho'],
      allowMultiChoices: true,
      allowAddNewOption: false,
      hideVotePreview: false,
      isAnonymous: false,
      expiresInHours: 24,
    },
  );
  assert.equal(
    validatePollCreateBody({ groupId: '7', question: 'Q', options: ['A'] }).ok,
    false,
  );
  assert.equal(
    validatePollCreateBody({
      groupId: '7',
      question: 'Q',
      options: Array.from({ length: MAX_POLL_OPTIONS + 1 }, (_, index) => `${index}`),
    }).ok,
    false,
  );
  assert.equal(
    validatePollCreateBody({ groupId: '7', question: 'Q', options: ['A', 'a'] }).ok,
    false,
  );
  assert.equal(
    validatePollCreateBody({ groupId: '7', question: 'Q', options: ['A', 'B '] }).ok,
    true,
  );
  assert.equal(
    validatePollCreateBody({
      groupId: '7',
      question: 'Q',
      options: ['A', 'x'.repeat(MAX_POLL_OPTION_LENGTH + 1)],
    }).ok,
    false,
  );
  assert.equal(
    validatePollCreateBody({
      groupId: '7',
      question: 'Q',
      options: ['A', 'B'],
      expiresInHours: MAX_POLL_EXPIRY_HOURS + 1,
    }).ok,
    false,
  );
  assert.equal(
    validatePollCreateBody({ groupId: '7', question: 'Q', options: ['A', 'B'], isAnonymous: 'yes' }).ok,
    false,
  );
  assert.equal(validatePollCreateBody({ groupId: 'x', question: 'Q', options: ['A', 'B'] }).ok, false);
});

test('validateNoteCreateBody requires a title and optional pin flag', () => {
  assert.deepEqual(validateNoteCreateBody({ groupId: '7', title: ' Plan ' }), {
    ok: true,
    groupId: '7',
    title: 'Plan',
    pinned: false,
  });
  assert.deepEqual(
    validateNoteCreateBody({ groupId: '7', title: 'Plan', pinned: true }),
    { ok: true, groupId: '7', title: 'Plan', pinned: true },
  );
  assert.equal(validateNoteCreateBody({ groupId: '7' }).ok, false);
  assert.equal(validateNoteCreateBody({ groupId: '7', title: '  ' }).ok, false);
  assert.equal(validateNoteCreateBody({ title: 'Plan' }).ok, false);
  assert.equal(
    validateNoteCreateBody({ groupId: '7', title: 'Plan', pinned: 'yes' }).ok,
    false,
  );
});

test('validateReminderCreateBody bounds the start time and repeat mode', () => {
  assert.deepEqual(
    validateReminderCreateBody({
      chatId: '11',
      title: ' Call ',
      startsInMinutes: 60,
      repeat: 'daily',
      emoji: '⏰',
    }),
    {
      ok: true,
      chatId: '11',
      threadType: 'user',
      title: 'Call',
      emoji: '⏰',
      startsInMinutes: 60,
      repeat: 'daily',
    },
  );
  assert.deepEqual(
    validateReminderCreateBody({
      chatId: '22',
      threadType: 'group',
      title: 'Call',
      startsInMinutes: 1,
    }),
    {
      ok: true,
      chatId: '22',
      threadType: 'group',
      title: 'Call',
      emoji: '',
      startsInMinutes: 1,
      repeat: 'none',
    },
  );
  assert.equal(
    validateReminderCreateBody({ chatId: '11', title: 'Call', startsInMinutes: 0 }).ok,
    false,
  );
  assert.equal(
    validateReminderCreateBody({ chatId: '11', title: 'Call' }).ok,
    false,
  );
  assert.equal(
    validateReminderCreateBody({
      chatId: '11',
      title: 'Call',
      startsInMinutes: MAX_REMINDER_MINUTES + 1,
    }).ok,
    false,
  );
  assert.equal(
    validateReminderCreateBody({ chatId: '11', title: 'Call', startsInMinutes: 60, repeat: 'hourly' }).ok,
    false,
  );
  assert.equal(
    validateReminderCreateBody({ chatId: '11', title: 'Call', startsInMinutes: 60, repeat: 'daily', emoji: 'x'.repeat(MAX_REMINDER_EMOJI_LENGTH + 1) }).ok,
    false,
  );
});

test('validatePinBody requires an explicit pinned boolean', () => {
  assert.deepEqual(validatePinBody({ chatId: '11', pinned: true }), {
    ok: true,
    chatId: '11',
    threadType: 'user',
    pinned: true,
  });
  assert.equal(validatePinBody({ chatId: '11' }).ok, false);
  assert.equal(validatePinBody({ chatId: '11', pinned: 'yes' }).ok, false);
  assert.equal(validatePinBody({ pinned: true }).ok, false);
});

test('validateMuteBody defaults to a permanent mute and bounds names', () => {
  assert.deepEqual(validateMuteBody({ chatId: '11' }), {
    ok: true,
    chatId: '11',
    threadType: 'user',
    action: 'mute',
    duration: 'forever',
  });
  assert.deepEqual(
    validateMuteBody({ chatId: '22', threadType: 'group', action: 'unmute' }),
    {
      ok: true,
      chatId: '22',
      threadType: 'group',
      action: 'unmute',
      duration: 'forever',
    },
  );
  assert.equal(validateMuteBody({ chatId: '11', action: 'quiet' }).ok, false);
  assert.equal(validateMuteBody({ chatId: '11', duration: 'ten_minutes' }).ok, false);
  assert.equal(validateMuteBody({}).ok, false);
});

test('validateLabelsBody needs a thread and label only for a change', () => {
  assert.deepEqual(validateLabelsBody({ action: 'list' }), {
    ok: true,
    action: 'list',
    chatId: '',
    threadType: 'user',
    labelId: '',
  });
  assert.deepEqual(
    validateLabelsBody({ action: 'add', chatId: '11', labelId: 3 }),
    { ok: true, action: 'add', chatId: '11', threadType: 'user', labelId: '3' },
  );
  assert.deepEqual(
    validateLabelsBody({ action: 'remove', chatId: '22', threadType: 'group', labelId: '4' }),
    { ok: true, action: 'remove', chatId: '22', threadType: 'group', labelId: '4' },
  );
  assert.equal(validateLabelsBody({ action: 'add', chatId: '11' }).ok, false);
  assert.equal(validateLabelsBody({ action: 'remove', labelId: 3 }).ok, false);
  assert.equal(validateLabelsBody({ action: 'delete', chatId: '11', labelId: 3 }).ok, false);
  assert.equal(validateLabelsBody(undefined).ok, false);
});

test('validateUnreadMarkBody defaults to adding the mark', () => {
  assert.deepEqual(validateUnreadMarkBody({ chatId: '11' }), {
    ok: true,
    chatId: '11',
    threadType: 'user',
    unread: true,
  });
  assert.deepEqual(validateUnreadMarkBody({ chatId: '11', unread: false }), {
    ok: true,
    chatId: '11',
    threadType: 'user',
    unread: false,
  });
  assert.equal(validateUnreadMarkBody({ chatId: '11', unread: 'no' }).ok, false);
  assert.equal(validateUnreadMarkBody({}).ok, false);
});

test('conversationIds accepts raw ids and small objects', () => {
  assert.deepEqual(
    conversationIds(['11', 22, { id: '33' }, { conversationId: '44' }, { threadId: '55' }, 'bad', null]),
    ['11', '22', '33', '44', '55'],
  );
  assert.deepEqual(conversationIds(null), []);
});

test('conversationEntries merges friends, groups and flags', () => {
  const entries = conversationEntries({
    friends: [
      { userId: '1', displayName: 'Nam' },
      { userId: '2', displayName: 'Lan' },
    ],
    groups: [{ groupId: '3', name: 'Team', memberCount: 9 }],
    pinned: ['2'],
    archived: [{ id: '3' }],
  });

  assert.deepEqual(entries, [
    { kind: 'dm', id: '2', name: 'Lan', member_count: 0, pinned: true, archived: false },
    { kind: 'group', id: '3', name: 'Team', member_count: 9, pinned: false, archived: true },
    { kind: 'dm', id: '1', name: 'Nam', member_count: 0, pinned: false, archived: false },
  ]);
});

test('groupHistoryRecords shapes self and inbound messages oldest first', () => {
  const records = groupHistoryRecords([
    {
      isSelf: true,
      data: {
        msgId: '11',
        uidFrom: '42',
        dName: 'Me',
        content: { title: 'photo.jpg', type: 'chat.photo', params: '{"fileSize":12}' },
        ts: '1700000001000',
      },
    },
    {
      isSelf: false,
      data: { msgId: '10', uidFrom: '77', dName: 'Nam', content: 'hello', ts: '1700000000000' },
    },
    { isSelf: false, data: { msgId: '', content: 'ignored' } },
  ]);

  assert.deepEqual(records, [
    {
      message_id: '10',
      sender_id: '77',
      sender_name: 'Nam',
      text: 'hello',
      ts: 1_700_000_000_000,
      type: 'text',
      direction: 'in',
    },
    {
      message_id: '11',
      sender_id: '42',
      sender_name: 'Me',
      text: 'photo.jpg',
      ts: 1_700_000_001_000,
      type: 'image',
      direction: 'out',
    },
  ]);
});

test('groupInfoRecord marks creator, admins and the member limit', () => {
  const record = groupInfoRecord(
    {
      groupId: '9',
      name: 'Team',
      desc: 'desc',
      totalMember: 3,
      createdTime: 100,
      creatorId: '1',
      adminIds: ['2'],
      memberIds: ['1', '2', '3'],
    },
    {
      1: { displayName: 'Owner' },
      2: { displayName: 'Admin' },
      3: { zaloName: 'Member' },
    },
    { memberLimit: 2 },
  );

  assert.deepEqual(record, {
    group_id: '9',
    name: 'Team',
    description: 'desc',
    member_count: 3,
    created_time: 100,
    creator_id: '1',
    members: [
      { user_id: '1', display_name: 'Owner', role: 'creator' },
      { user_id: '2', display_name: 'Admin', role: 'admin' },
    ],
  });
});

test('reminderRecords and noteRecords keep only their own shapes', () => {
  assert.deepEqual(
    reminderRecords([
      {
        id: '5',
        creatorId: '1',
        startTime: 1_700_000_000,
        repeat: 1,
        params: { title: 'Standup' },
      },
      null,
    ]),
    [
      {
        reminder_id: '5',
        title: 'Standup',
        creator_id: '1',
        start_time: 1_700_000_000,
        repeat: 1,
      },
    ],
  );

  assert.deepEqual(
    noteRecords([
      { boardType: 1, data: { id: '7', creatorId: '1', createTime: 10, editTime: 20, params: { title: 'Plan' } } },
      { boardType: 3, data: { id: '8', params: { title: 'Poll' } } },
    ]),
    [
      {
        note_id: '7',
        title: 'Plan',
        creator_id: '1',
        created_time: 10,
        updated_time: 20,
      },
    ],
  );
});

test('pollRecord and unreadRecords normalize upstream payloads', () => {
  assert.deepEqual(
    pollRecord({
      poll_id: 12,
      question: 'Lunch?',
      closed: false,
      is_anonymous: true,
      allow_multi_choices: false,
      num_vote: 3,
      created_time: 100,
      options: [{ option_id: 1, content: 'Pizza', votes: 2, voted: true }],
    }),
    {
      poll_id: '12',
      question: 'Lunch?',
      options: [{ option_id: 1, content: 'Pizza', votes: 2, voted: true }],
      closed: false,
      anonymous: true,
      multi_choice: false,
      vote_count: 3,
      created_time: 100,
    },
  );

  assert.deepEqual(
    unreadRecords({
      data: {
        convsUser: [{ id: 42, cliMsgId: 9, ts: 100 }],
        convsGroup: [{ id: '43', cliMsgId: 8, ts: 200 }, { id: 'x' }],
      },
    }),
    {
      direct: [{ thread_id: '42', last_message_id: '9', ts: 100 }],
      groups: [{ thread_id: '43', last_message_id: '8', ts: 200 }],
    },
  );
});

test('validateSendMediaBody accepts file, video, and voice kinds', () => {
  const mp4 = Buffer.concat([
    Buffer.from([0, 0, 0, 0x20]),
    Buffer.from('ftypisom', 'latin1'),
    Buffer.alloc(16, 0x01),
  ]);
  const m4a = Buffer.concat([
    Buffer.from([0, 0, 0, 0x20]),
    Buffer.from('ftypM4A ', 'latin1'),
    Buffer.alloc(16, 0x01),
  ]);
  const ogg = Buffer.concat([Buffer.from('OggS', 'latin1'), Buffer.alloc(16, 0x01)]);

  const file = validateSendMediaBody({
    chatId: '42',
    kind: 'file',
    fileName: '../report.pdf',
    contentBase64: Buffer.from('%PDF-1.4 report').toString('base64'),
  });
  assert.equal(file.ok, true);
  assert.equal(file.kind, 'file');
  assert.equal(file.fileName, 'report.pdf');
  assert.equal(file.mimeType, 'application/octet-stream');

  const video = validateSendMediaBody({
    chatId: '42',
    kind: 'video',
    fileName: 'clip.MP4',
    contentBase64: mp4.toString('base64'),
  });
  assert.equal(video.ok, true);
  assert.equal(video.kind, 'video');
  assert.equal(video.fileName, 'clip.mp4');

  const voice = validateSendMediaBody({
    chatId: '42',
    kind: 'voice',
    fileName: 'note.m4a',
    contentBase64: m4a.toString('base64'),
  });
  assert.equal(voice.ok, true);
  assert.equal(voice.kind, 'voice');

  const opus = validateSendMediaBody({
    chatId: '42',
    kind: 'voice',
    fileName: 'note.opus',
    contentBase64: ogg.toString('base64'),
  });
  assert.equal(opus.ok, true);
});

test('validateSendMediaBody rejects media that does not match its kind', () => {
  const png = pngPayload(8);
  const mp4 = Buffer.concat([
    Buffer.from([0, 0, 0, 0x20]),
    Buffer.from('ftypisom', 'latin1'),
    Buffer.alloc(16, 0x01),
  ]);
  const base = { chatId: '42', contentBase64: png.toString('base64') };

  assert.equal(validateSendMediaBody({ ...base, kind: 'archive' }).ok, false);
  // A plain file never reuses an image, video, or voice extension.
  assert.equal(
    validateSendMediaBody({ ...base, kind: 'file', fileName: 'photo.png' }).ok,
    false,
  );
  assert.equal(
    validateSendMediaBody({ ...base, kind: 'file', fileName: 'no-extension' }).ok,
    false,
  );
  assert.equal(
    validateSendMediaBody({
      chatId: '42',
      kind: 'video',
      fileName: 'clip.mp4',
      contentBase64: png.toString('base64'),
    }).ok,
    false,
  );
  assert.equal(
    validateSendMediaBody({
      chatId: '42',
      kind: 'video',
      fileName: 'clip.avi',
      contentBase64: mp4.toString('base64'),
    }).ok,
    false,
  );
  assert.equal(
    validateSendMediaBody({
      chatId: '42',
      kind: 'voice',
      fileName: 'note.m4a',
      contentBase64: png.toString('base64'),
    }).ok,
    false,
  );
  assert.equal(
    validateSendMediaBody({
      chatId: '42',
      kind: 'voice',
      fileName: 'note.wav',
      contentBase64: Buffer.from('RIFF\0\0\0\0WAVE').toString('base64'),
    }).ok,
    false,
  );
});

test('validateSendMessageBody accepts a quote and group mentions', () => {
  const quoted = validateSendMessageBody({
    chatId: '42',
    message: 'answer',
    quoteMessageId: ' 99 ',
  });
  assert.equal(quoted.ok, true);
  assert.equal(quoted.quoteMessageId, '99');

  const mentioned = validateSendMessageBody({
    chatId: '42',
    threadType: 'group',
    message: 'hi @Lan',
    mentions: ['77', '78'],
  });
  assert.equal(mentioned.ok, true);
  assert.deepEqual(mentioned.mentions, ['77', '78']);
});

test('validateSendMessageBody rejects malformed reply options', () => {
  const base = { chatId: '42', message: 'hello' };
  assert.equal(validateSendMessageBody({ ...base, quoteMessageId: 'abc' }).ok, false);
  assert.equal(validateSendMessageBody({ ...base, quoteMessageId: 99 }).ok, false);
  assert.equal(validateSendMessageBody({ ...base, mentions: ['77'] }).ok, false);
  assert.equal(
    validateSendMessageBody({ ...base, threadType: 'group', mentions: [] }).ok,
    false,
  );
  assert.equal(
    validateSendMessageBody({ ...base, threadType: 'group', mentions: ['77', '77'] }).ok,
    false,
  );
  assert.equal(
    validateSendMessageBody({
      ...base,
      threadType: 'group',
      mentions: Array.from({ length: MAX_MENTIONS + 1 }, (_, index) => String(index + 1)),
    }).ok,
    false,
  );
});

test('mentionPayloads locates every @name and rejects a missing one', () => {
  const built = mentionPayloads('hi @Lan and @Minh, see this', [
    { uid: '77', name: 'Lan' },
    { uid: '78', name: 'Minh' },
  ]);
  assert.deepEqual(built, {
    ok: true,
    mentions: [
      { pos: 3, uid: '77', len: 4 },
      { pos: 12, uid: '78', len: 5 },
    ],
  });

  const repeated = mentionPayloads('@Lan ping @Lan', [
    { uid: '77', name: 'Lan' },
    { uid: '78', name: 'Lan' },
  ]);
  assert.equal(repeated.ok, true);
  assert.deepEqual(
    repeated.mentions.map((entry) => entry.pos),
    [0, 10],
  );

  assert.equal(mentionPayloads('hi there', [{ uid: '77', name: 'Lan' }]).ok, false);
  assert.equal(mentionPayloads('hi @Lan', [{ uid: '', name: 'Lan' }]).ok, false);
  assert.equal(mentionPayloads('hi @Lan', [{ uid: '77', name: '' }]).ok, false);
});

test('validateSendStickerBody requires a keyword and a bounded index', () => {
  assert.deepEqual(
    validateSendStickerBody({ chatId: '42', threadType: 'group', keyword: ' cat ' }),
    {
      ok: true,
      chatId: '42',
      threadType: 'group',
      keyword: 'cat',
      index: 0,
    },
  );
  assert.equal(
    validateSendStickerBody({ chatId: '42', keyword: 'cat', index: 3 }).index,
    3,
  );
  assert.equal(validateSendStickerBody({ chatId: '42', keyword: '' }).ok, false);
  assert.equal(validateSendStickerBody({ chatId: '42', keyword: 'x'.repeat(101) }).ok, false);
  assert.equal(validateSendStickerBody({ chatId: '42', keyword: 'cat', index: -1 }).ok, false);
  assert.equal(
    validateSendStickerBody({
      chatId: '42',
      keyword: 'cat',
      index: MAX_STICKER_SEARCH_LIMIT,
    }).ok,
    false,
  );
});

test('validateSendCardBody accepts one visible user id', () => {
  assert.deepEqual(validateSendCardBody({ chatId: '42', userId: ' 77 ' }), {
    ok: true,
    chatId: '42',
    threadType: 'user',
    userId: '77',
    phoneNumber: '',
  });
  assert.equal(
    validateSendCardBody({ chatId: '42', userId: '77', phoneNumber: '+84912345678' })
      .phoneNumber,
    '+84912345678',
  );
  assert.equal(validateSendCardBody({ chatId: '42', userId: 'abc' }).ok, false);
  assert.equal(validateSendCardBody({ chatId: '42', userId: '77', phoneNumber: 'abc' }).ok, false);
  assert.equal(validateSendCardBody({ chatId: '42' }).ok, false);
});

test('validateForwardBody and validateUndoBody validate both ends', () => {
  assert.deepEqual(
    validateForwardBody({
      sourceChatId: '111',
      messageId: '9',
      chatId: '123',
      threadType: 'group',
    }),
    {
      ok: true,
      sourceChatId: '111',
      sourceThreadType: 'user',
      messageId: '9',
      chatId: '123',
      threadType: 'group',
    },
  );
  assert.equal(validateForwardBody({ sourceChatId: 'x', messageId: '9', chatId: '1' }).ok, false);
  assert.equal(validateForwardBody({ sourceChatId: '1', messageId: 'x', chatId: '1' }).ok, false);
  assert.equal(validateForwardBody({ sourceChatId: '1', messageId: '9' }).ok, false);

  assert.deepEqual(validateUndoBody({ chatId: '111', messageId: '9' }), {
    ok: true,
    chatId: '111',
    threadType: 'user',
    messageId: '9',
  });
  assert.equal(validateUndoBody({ chatId: '111' }).ok, false);
  assert.equal(validateUndoBody({ chatId: '111', messageId: 'x' }).ok, false);
});

test('undoExpired refuses messages outside the undo window', () => {
  const now = 1_700_000_000_000;
  assert.equal(undoExpired(now - 1_000, { now }), false);
  assert.equal(undoExpired(now - UNDO_MAX_AGE_MS, { now }), false);
  assert.equal(undoExpired(now - UNDO_MAX_AGE_MS - 1, { now }), true);
  assert.equal(undoExpired(0, { now }), true);
  assert.equal(undoExpired('bad', { now }), true);
});

test('messageRef and quotePayload shape a quotable message', () => {
  const reference = messageRef(
    {
      msgId: 9,
      cliMsgId: '1700000000000',
      uidFrom: '111',
      msgType: 'webchat',
      ts: 1_700_000_000_000,
      content: 'hello',
    },
    { threadId: '111', threadType: 'user', isSelf: false },
  );
  assert.equal(reference.msgId, '9');
  assert.equal(reference.cliMsgId, '1700000000000');
  assert.equal(reference.threadType, 'user');

  assert.deepEqual(quotePayload(reference), {
    content: 'hello',
    msgType: 'webchat',
    propertyExt: undefined,
    uidFrom: '111',
    msgId: '9',
    cliMsgId: '1700000000000',
    ts: 1_700_000_000_000,
    ttl: 0,
  });
  assert.equal(quotePayload(null), null);
  assert.equal(quotePayload({ msgId: '9' }), null);
  assert.equal(messageRef({}, { threadId: '1' }), null);
});

test('userInfoRecord accepts getUsers and findUser shapes', () => {
  assert.deepEqual(
    userInfoRecord({
      userId: '42',
      displayName: 'Nam',
      zaloName: 'nam',
      avatar: 'https://a',
      isFr: 1,
      gender: 1,
      sdob: '1990-01-01',
    }),
    {
      user_id: '42',
      display_name: 'Nam',
      zalo_name: 'nam',
      avatar: 'https://a',
      is_friend: true,
      gender: 1,
      birthday: '1990-01-01',
    },
  );
  assert.deepEqual(userInfoRecord({ uid: '43', display_name: 'Lan', zalo_name: 'lan' }), {
    user_id: '43',
    display_name: 'Lan',
    zalo_name: 'lan',
    avatar: '',
    is_friend: false,
    gender: 0,
    birthday: '',
  });
});

/* ------------------------------------------------------------------------- *
 * Group administration and account utilities (plan 020 P-B phase 4).
 * ------------------------------------------------------------------------- */

test('validateGroupMembersBody lists, adds, and removes within bounds', () => {
  assert.deepEqual(validateGroupMembersBody({ groupId: '123' }), {
    ok: true,
    action: 'list',
    groupId: '123',
    memberLimit: 50,
  });
  assert.deepEqual(
    validateGroupMembersBody({ action: 'list', groupId: '123', memberLimit: 100 }),
    { ok: true, action: 'list', groupId: '123', memberLimit: 100 },
  );
  assert.deepEqual(
    validateGroupMembersBody({ action: 'add', groupId: '123', memberIds: ['7', '7', '8'] }),
    { ok: true, action: 'add', groupId: '123', memberIds: ['7', '8'] },
  );
  assert.equal(MAX_GROUP_MEMBER_LIMIT, 100);
  assert.equal(MAX_GROUP_MEMBER_CHANGE, 20);
});

test('validateGroupMembersBody rejects malformed groups, lists, and limits', () => {
  const bad = [
    null,
    {},
    { groupId: 'abc' },
    { action: 'rename', groupId: '123' },
    { groupId: '123', memberLimit: 101 },
    { groupId: '123', memberLimit: 0, action: 'list', memberIds: ['1'] },
    { action: 'add', groupId: '123', memberIds: [] },
    { action: 'add', groupId: '123', memberIds: ['x'] },
    { action: 'remove', groupId: '123' },
    {
      action: 'add',
      groupId: '123',
      memberIds: Array.from({ length: MAX_GROUP_MEMBER_CHANGE + 1 }, (_, i) => String(i + 1)),
    },
    { action: 'list', groupId: '123', memberIds: ['1'] },
  ];
  for (const body of bad) {
    assert.equal(validateGroupMembersBody(body).ok, false, JSON.stringify(body));
  }
});

test('validateGroupSettingsBody takes a rename or a partial settings change', () => {
  assert.deepEqual(
    validateGroupSettingsBody({ action: 'rename', groupId: '123', name: '  Team One  ' }),
    { ok: true, action: 'rename', groupId: '123', name: 'Team One' },
  );
  assert.deepEqual(
    validateGroupSettingsBody({
      action: 'update',
      groupId: '123',
      settings: { lockSendMsg: true, joinAppr: false },
    }),
    {
      ok: true,
      action: 'update',
      groupId: '123',
      settings: { lockSendMsg: true, joinAppr: false },
    },
  );
  // The rename name is capped and must not be empty.
  assert.equal(MAX_GROUP_NAME_LENGTH, 100);
  assert.equal(
    validateGroupSettingsBody({ action: 'rename', groupId: '123', name: ' x'.repeat(60) }).ok,
    false,
  );
  const bad = [
    {},
    { action: 'rename', groupId: '123' },
    { action: 'rename', groupId: '123', name: '   ' },
    { action: 'update', groupId: '123' },
    { action: 'update', groupId: '123', settings: {} },
    { action: 'update', groupId: '123', settings: { unknown: true } },
    { action: 'update', groupId: '123', settings: { lockSendMsg: 'yes' } },
    { action: 'update', groupId: '123', settings: [] },
  ];
  for (const body of bad) {
    assert.equal(validateGroupSettingsBody(body).ok, false, JSON.stringify(body));
  }
  assert.deepEqual(GROUP_SETTING_ACTIONS, ['rename', 'update']);
  assert.equal(GROUP_SETTING_KEYS.length, 9);
});

test('validateGroupRolesBody needs a deputy action and member ids', () => {
  assert.deepEqual(
    validateGroupRolesBody({ action: 'add_deputy', groupId: '5', memberIds: ['7'] }),
    { ok: true, action: 'add_deputy', groupId: '5', memberIds: ['7'] },
  );
  for (const body of [
    { groupId: '5', memberIds: ['7'] },
    { action: 'add_deputy', groupId: '5', memberIds: [] },
    { action: 'remove_deputy', groupId: '5' },
  ]) {
    assert.equal(validateGroupRolesBody(body).ok, false, JSON.stringify(body));
  }
  assert.deepEqual(GROUP_ROLE_ACTIONS, ['add_deputy', 'remove_deputy']);
});

test('validateGroupPendingBody lists or reviews join requests', () => {
  assert.deepEqual(validateGroupPendingBody({ groupId: '5' }), {
    ok: true,
    action: 'list',
    groupId: '5',
    limit: 50,
  });
  assert.deepEqual(
    validateGroupPendingBody({ action: 'approve', groupId: '5', memberIds: ['9'] }),
    { ok: true, action: 'approve', groupId: '5', memberIds: ['9'] },
  );
  assert.equal(MAX_PENDING_MEMBERS, 100);
  for (const body of [
    { groupId: '5', limit: 101 },
    { action: 'approve', groupId: '5' },
    { action: 'list', groupId: '5', memberIds: ['9'] },
  ]) {
    assert.equal(validateGroupPendingBody(body).ok, false, JSON.stringify(body));
  }
  assert.deepEqual(GROUP_PENDING_ACTIONS, ['list', 'approve', 'reject']);
});

test('validateGroupLinkBody accepts get, enable, and disable', () => {
  assert.deepEqual(validateGroupLinkBody({ groupId: '5' }), {
    ok: true,
    action: 'get',
    groupId: '5',
  });
  assert.deepEqual(validateGroupLinkBody({ action: 'disable', groupId: '5' }), {
    ok: true,
    action: 'disable',
    groupId: '5',
  });
  assert.equal(validateGroupLinkBody({ action: 'rotate', groupId: '5' }).ok, false);
  assert.deepEqual(GROUP_LINK_ACTIONS, ['get', 'enable', 'disable']);
});

test('validateCreateGroupBody requires a name and one to twenty members', () => {
  assert.deepEqual(validateCreateGroupBody({ name: ' Team ', memberIds: ['7', '8'] }), {
    ok: true,
    name: 'Team',
    memberIds: ['7', '8'],
  });
  for (const body of [
    { name: '', memberIds: ['7'] },
    { name: 'Team', memberIds: [] },
    { name: 'Team', memberIds: ['x'] },
    { name: 'Team' },
  ]) {
    assert.equal(validateCreateGroupBody(body).ok, false, JSON.stringify(body));
  }
});

test('validateFriendRequestsBody validates each action', () => {
  assert.deepEqual(validateFriendRequestsBody({ action: 'list_sent' }), {
    ok: true,
    action: 'list_sent',
    limit: 50,
  });
  assert.deepEqual(validateFriendRequestsBody({ action: 'accept', userId: '42' }), {
    ok: true,
    action: 'accept',
    userId: '42',
  });
  assert.deepEqual(
    validateFriendRequestsBody({ action: 'send', userId: '42', message: '  hi  ' }),
    { ok: true, action: 'send', userId: '42', message: 'hi' },
  );
  assert.deepEqual(validateFriendRequestsBody({ action: 'send', userId: '42' }), {
    ok: true,
    action: 'send',
    userId: '42',
    message: '',
  });
  assert.equal(MAX_FRIEND_REQUEST_MESSAGE_LENGTH, 500);
  for (const body of [
    {},
    { action: 'send' },
    { action: 'accept', userId: 'abc' },
    { action: 'send', userId: '42', message: 'x'.repeat(501) },
    { action: 'list_sent', limit: 0 },
  ]) {
    assert.equal(validateFriendRequestsBody(body).ok, false, JSON.stringify(body));
  }
  assert.deepEqual(FRIEND_REQUEST_ACTIONS, [
    'list_sent',
    'list_received',
    'accept',
    'reject',
    'send',
    'undo',
  ]);
});

test('validateFriendAliasBody handles list, set, and remove', () => {
  assert.deepEqual(validateFriendAliasBody({}), {
    ok: true,
    action: 'list',
    page: 1,
    count: 50,
  });
  assert.equal(MAX_ALIAS_PAGE_SIZE, 200);
  assert.deepEqual(validateFriendAliasBody({ action: 'remove', friendId: '42' }), {
    ok: true,
    action: 'remove',
    friendId: '42',
  });
  assert.deepEqual(
    validateFriendAliasBody({ action: 'set', friendId: '42', alias: ' Boss ' }),
    { ok: true, action: 'set', friendId: '42', alias: 'Boss' },
  );
  assert.equal(MAX_FRIEND_ALIAS_LENGTH, 64);
  for (const body of [
    { action: 'set', friendId: '42' },
    { action: 'set', friendId: '42', alias: 'x'.repeat(65) },
    { action: 'remove' },
    { action: 'list', count: 201 },
  ]) {
    assert.equal(validateFriendAliasBody(body).ok, false, JSON.stringify(body));
  }
  assert.deepEqual(FRIEND_ALIAS_ACTIONS, ['list', 'set', 'remove']);
});

test('validateQuickReplyBody bounds keywords, titles, and item ids', () => {
  assert.deepEqual(validateQuickReplyBody({}), { ok: true, action: 'list' });
  assert.deepEqual(validateQuickReplyBody({ action: 'remove', itemId: 3 }), {
    ok: true,
    action: 'remove',
    itemId: 3,
  });
  assert.deepEqual(
    validateQuickReplyBody({ action: 'add', keyword: ' hi ', title: 'Hello' }),
    { ok: true, action: 'add', keyword: 'hi', title: 'Hello' },
  );
  assert.deepEqual(
    validateQuickReplyBody({ action: 'update', itemId: 3, keyword: 'hi', title: 'Hello' }),
    { ok: true, action: 'update', itemId: 3, keyword: 'hi', title: 'Hello' },
  );
  assert.equal(MAX_QUICK_REPLY_TITLE_LENGTH, 1_000);
  for (const body of [
    { action: 'add', keyword: '', title: 'Hi' },
    { action: 'add', keyword: 'hi', title: 'x'.repeat(1_001) },
    { action: 'update', keyword: 'hi', title: 'Hi' },
    { action: 'remove' },
  ]) {
    assert.equal(validateQuickReplyBody(body).ok, false, JSON.stringify(body));
  }
  assert.deepEqual(QUICK_REPLY_ACTIONS, ['list', 'add', 'update', 'remove']);
});

test('groupMembersRecord marks roles and reports the remaining members', () => {
  const record = groupMembersRecord(
    {
      groupId: '5',
      name: 'Team',
      totalMember: 3,
      creatorId: '1',
      adminIds: ['2'],
      memberIds: ['1', '2', '3'],
    },
    {
      1: { displayName: 'Owner' },
      2: { displayName: 'Admin' },
      3: { displayName: 'Member' },
    },
    { memberLimit: 2 },
  );
  assert.deepEqual(record, {
    group_id: '5',
    name: 'Team',
    member_count: 3,
    members: [
      { user_id: '1', display_name: 'Owner', role: 'creator' },
      { user_id: '2', display_name: 'Admin', role: 'admin' },
    ],
    has_more: true,
  });
  assert.equal(groupMembersRecord({ memberIds: [] }, null, { memberLimit: 6 }).has_more, false);
});

test('groupSettingsOptions keeps the current value of every unmentioned key', () => {
  const current = { lockSendMsg: 1, joinAppr: 0, blockName: 1 };
  assert.deepEqual(groupSettingsOptions(current, { joinAppr: true }), {
    blockName: true,
    signAdminMsg: false,
    setTopicOnly: false,
    enableMsgHistory: false,
    joinAppr: true,
    lockCreatePost: false,
    lockCreatePoll: false,
    lockSendMsg: true,
    lockViewMember: false,
  });
  assert.deepEqual(groupSettingsRecord(current), {
    blockName: true,
    signAdminMsg: false,
    setTopicOnly: false,
    enableMsgHistory: false,
    joinAppr: false,
    lockCreatePost: false,
    lockCreatePoll: false,
    lockSendMsg: true,
    lockViewMember: false,
  });
});

test('pendingMemberRecords and reviewResults shape join requests', () => {
  const pending = pendingMemberRecords(
    [
      { uid: '7', dpn: 'Lan', avatar: 'https://a' },
      { uid: 'x', dpn: 'Nope' },
    ],
    { limit: 1 },
  );
  assert.deepEqual(pending, [{ user_id: '7', display_name: 'Lan' }]);
  assert.deepEqual(
    reviewResults(['7', '8'], { 7: 0, 8: 178 }),
    [
      { user_id: '7', status: 'success' },
      { user_id: '8', status: 'already_member' },
    ],
  );
  assert.equal(reviewStatusName(170), 'not_pending');
  assert.equal(reviewStatusName(166), 'no_permission');
  assert.equal(reviewStatusName(999), 'unknown');
});

test('memberChangeRecord reports changed and failed ids only', () => {
  assert.deepEqual(memberChangeRecord(['7', '8', '9'], ['8', '99']), {
    requested: 3,
    changed: ['7', '9'],
    failed: ['8'],
  });
  assert.deepEqual(memberChangeRecord(['7'], null), {
    requested: 1,
    changed: ['7'],
    failed: [],
  });
});

test('sentFriendRequestRecords keeps display fields and caps the list', () => {
  const records = sentFriendRequestRecords({
    42: {
      userId: '42',
      displayName: 'Nam',
      zaloName: 'nam',
      fReqInfo: { message: 'Hello', time: 1_000 },
    },
    43: { userId: '43', zaloName: 'lan', fReqInfo: { time: 2_000 } },
  });
  assert.deepEqual(records, [
    { user_id: '42', display_name: 'Nam', message: 'Hello', ts: 1_000 },
    { user_id: '43', display_name: 'lan', message: '', ts: 2_000 },
  ]);
  assert.equal(sentFriendRequestRecords({ 42: {} }, { limit: 1 }).length, 1);
});

test('receivedFriendRequestRecords keeps only incoming requests', () => {
  const response = {
    recommItems: [
      {
        dataInfo: {
          userId: '7',
          displayName: 'Lan',
          recommType: 2,
          recommTime: 500,
          recommInfo: { message: 'Add me' },
        },
      },
      { dataInfo: { userId: '8', displayName: 'Suggested', recommType: 1 } },
      { dataInfo: { userId: 'x', displayName: 'Bad', recommType: 2 } },
    ],
  };
  assert.deepEqual(receivedFriendRequestRecords(response), [
    { user_id: '7', display_name: 'Lan', message: 'Add me', ts: 500 },
  ]);
});

test('aliasRecords and quickReplyRecords shape their responses', () => {
  assert.deepEqual(
    aliasRecords({ items: [{ userId: '7', alias: 'Boss' }, { userId: 'x', alias: 'No' }] }),
    [{ user_id: '7', alias: 'Boss' }],
  );
  const quick = quickReplyRecords([
    {
      id: 3,
      keyword: 'hi',
      message: { title: 'Hello there' },
      createdTime: 10,
      lastModified: 20,
    },
    { id: 0, keyword: 'bad' },
  ]);
  assert.deepEqual(quick, [
    {
      item_id: 3,
      keyword: 'hi',
      title: 'Hello there',
      created_time: 10,
      updated_time: 20,
    },
  ]);
  assert.equal(MAX_QUICK_REPLIES, 200);
});
