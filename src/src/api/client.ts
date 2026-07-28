export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? window.location.origin).replace(/\/+$/, '');

type ApiEnvelope<T> = {
  success?: boolean;
  data?: T;
  message?: string;
  error?: string;
  status_code?: number;
  statusCode?: number;
};

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly details?: unknown,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export function buildApiUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) return path;
  return `${API_BASE_URL}/${path.replace(/^\/+/, '')}`;
}

function authenticatedHeaders(init?: RequestInit, includeJson = true): Headers {
  const headers = new Headers(init?.headers);
  if (!headers.has('Accept')) headers.set('Accept', 'application/json');
  if (includeJson && init?.body != null && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  return headers;
}

async function responseBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return undefined;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

function messageFrom(body: unknown, response: Response): string {
  if (body && typeof body === 'object') {
    const record = body as Record<string, unknown>;
    if (typeof record.message === 'string' && record.message) return record.message;
    if (typeof record.error === 'string' && record.error) return record.error;
  }
  return response.statusText || `Request failed (${response.status})`;
}

export async function requestRaw(path: string, init: RequestInit = {}): Promise<Response> {
  const response = await fetch(buildApiUrl(path), {
    ...init,
    credentials: 'same-origin',
    headers: authenticatedHeaders(init),
  });
  if (response.ok) return response;

  const body = await responseBody(response);
  throw new ApiError(response.status, messageFrom(body, response), body);
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await requestRaw(path, init);
  if (response.status === 204 || response.headers.get('content-length') === '0') return undefined as T;

  const body = await responseBody(response);
  if (body == null) return undefined as T;
  if (typeof body !== 'object') return body as T;

  const envelope = body as ApiEnvelope<T>;
  if (envelope.success === false) {
    throw new ApiError(
      envelope.status_code ?? envelope.statusCode ?? response.status,
      envelope.message ?? envelope.error ?? 'Request failed',
      body,
    );
  }
  if ('data' in envelope) return envelope.data as T;
  return body as T;
}

/** Pagination metadata as returned alongside `data` in list envelopes. */
export type ResponsePagination = {
  page?: number;
  limit?: number;
  total_items?: number;
  total_pages?: number;
};

/** Like `request`, but also surfaces the envelope's `pagination` block. */
export async function requestWithMeta<T>(
  path: string,
  init: RequestInit = {},
): Promise<{ data: T; pagination?: ResponsePagination }> {
  const response = await requestRaw(path, init);
  if (response.status === 204 || response.headers.get('content-length') === '0') {
    return { data: undefined as T };
  }

  const body = await responseBody(response);
  if (body == null || typeof body !== 'object') return { data: body as T };

  const envelope = body as ApiEnvelope<T> & { pagination?: ResponsePagination };
  if (envelope.success === false) {
    throw new ApiError(
      envelope.status_code ?? envelope.statusCode ?? response.status,
      envelope.message ?? envelope.error ?? 'Request failed',
      body,
    );
  }
  const data = ('data' in envelope ? envelope.data : body) as T;
  return { data, pagination: envelope.pagination };
}

export async function requestMultipart<T>(path: string, body: FormData, init: RequestInit = {}): Promise<T> {
  const headers = authenticatedHeaders({ ...init, body }, false);
  headers.delete('Content-Type');
  return request<T>(path, { ...init, method: init.method ?? 'POST', body, headers });
}

export type UploadProgress = {
  loaded: number;
  total?: number;
  percent?: number;
};

function parseXhrBody(text: string): unknown {
  if (!text) return undefined;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

function xhrMessageFrom(body: unknown, status: number, statusText: string): string {
  if (body && typeof body === 'object') {
    const record = body as Record<string, unknown>;
    if (typeof record.message === 'string' && record.message) return record.message;
    if (typeof record.error === 'string' && record.error) return record.error;
  }
  return statusText || `Request failed (${status})`;
}

function unwrapXhrEnvelope<T>(body: unknown, status: number): T {
  if (body == null) return undefined as T;
  if (typeof body !== 'object') return body as T;

  const envelope = body as ApiEnvelope<T>;
  if (envelope.success === false) {
    throw new ApiError(
      envelope.status_code ?? envelope.statusCode ?? status,
      envelope.message ?? envelope.error ?? 'Request failed',
      body,
    );
  }
  if ('data' in envelope) return envelope.data as T;
  return body as T;
}

export function requestMultipartWithProgress<T>(
  path: string,
  body: FormData,
  onProgress?: (progress: UploadProgress) => void,
  init: RequestInit = {},
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const headers = authenticatedHeaders({ ...init, body }, false);
    headers.delete('Content-Type');

    const method = init.method ?? 'POST';
    xhr.open(method, buildApiUrl(path), true);
    xhr.responseType = 'text';
    headers.forEach((value, key) => xhr.setRequestHeader(key, value));

    const abort = () => xhr.abort();
    if (init.signal) {
      if (init.signal.aborted) {
        reject(new ApiError(0, 'Request was cancelled'));
        return;
      }
      init.signal.addEventListener('abort', abort, { once: true });
    }

    xhr.upload.onprogress = (event) => {
      if (!onProgress) return;
      const total = event.lengthComputable ? event.total : undefined;
      onProgress({
        loaded: event.loaded,
        total,
        percent: total ? Math.min(100, Math.round((event.loaded / total) * 100)) : undefined,
      });
    };

    xhr.onload = () => {
      init.signal?.removeEventListener('abort', abort);
      const bodyValue = parseXhrBody(typeof xhr.response === 'string' ? xhr.response : xhr.responseText);
      if (xhr.status < 200 || xhr.status >= 300) {
        reject(new ApiError(xhr.status, xhrMessageFrom(bodyValue, xhr.status, xhr.statusText), bodyValue));
        return;
      }
      try {
        resolve(unwrapXhrEnvelope<T>(bodyValue, xhr.status));
      } catch (cause) {
        reject(cause);
      }
    };
    xhr.onerror = () => {
      init.signal?.removeEventListener('abort', abort);
      reject(new ApiError(0, 'Network error while uploading'));
    };
    xhr.onabort = () => {
      init.signal?.removeEventListener('abort', abort);
      reject(new ApiError(0, 'Request was cancelled'));
    };

    xhr.send(body);
  });
}
