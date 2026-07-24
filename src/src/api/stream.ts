export type SSEEvent = {
  id?: string;
  event: string;
  data: unknown;
};

function parseFrame(frame: string): SSEEvent | null {
  let id: string | undefined;
  let event = 'message';
  const data: string[] = [];
  for (const rawLine of frame.split(/\r?\n/)) {
    const line = rawLine.trimEnd();
    if (!line || line.startsWith(':')) continue;
    if (line.startsWith('id:')) id = line.slice(3).trim();
    if (line.startsWith('event:')) event = line.slice(6).trim();
    if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
  }
  if (data.length === 0) return null;
  const joined = data.join('\n');
  try {
    return { id, event, data: JSON.parse(joined) as unknown };
  } catch {
    return { id, event, data: joined };
  }
}

function separator(buffer: string): { index: number; length: number } | null {
  const lf = buffer.indexOf('\n\n');
  const crlf = buffer.indexOf('\r\n\r\n');
  if (lf < 0 && crlf < 0) return null;
  if (crlf >= 0 && (lf < 0 || crlf < lf)) return { index: crlf, length: 4 };
  return { index: lf, length: 2 };
}

export async function readSSE(
  response: Response,
  onEvent: (event: SSEEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  if (!response.body) throw new Error('SSE response body is unavailable.');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    for (;;) {
      if (signal?.aborted) throw new DOMException('The operation was aborted.', 'AbortError');
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      for (;;) {
        const found = separator(buffer);
        if (!found) break;
        const frame = buffer.slice(0, found.index);
        buffer = buffer.slice(found.index + found.length);
        const parsed = parseFrame(frame);
        if (parsed) onEvent(parsed);
      }
    }
    buffer += decoder.decode();
    const final = parseFrame(buffer);
    if (final) onEvent(final);
  } finally {
    reader.releaseLock();
  }
}
