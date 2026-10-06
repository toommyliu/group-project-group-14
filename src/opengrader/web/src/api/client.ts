import createClient from 'openapi-fetch';
import type { paths } from './schema';
import {
  parseValidationLocation,
  type ValidationLocation,
} from '@/lib/validation-source-line';

function serverMessage(body: unknown): string | undefined {
  if (!body || typeof body !== 'object' || !('detail' in body)) return;
  const { detail } = body;
  if (typeof detail === 'string') return detail.trim() || undefined;
  if (Array.isArray(detail)) {
    const messages = detail.flatMap((issue: unknown) =>
      issue &&
      typeof issue === 'object' &&
      'msg' in issue &&
      typeof issue.msg === 'string' &&
      issue.msg.trim()
        ? [issue.msg.trim()]
        : []
    );
    return messages.join('\n') || undefined;
  }
}

function fieldMessages(body: unknown): Partial<Record<string, string>> {
  if (!body || typeof body !== 'object' || !('detail' in body)) return {};
  if (!Array.isArray(body.detail)) return {};
  const issues: unknown[] = body.detail;
  const fields: Partial<Record<string, string>> = {};
  for (const issue of issues) {
    if (
      issue &&
      typeof issue === 'object' &&
      'loc' in issue &&
      Array.isArray(issue.loc) &&
      issue.loc[0] === 'body' &&
      typeof issue.loc[1] === 'string' &&
      'msg' in issue &&
      typeof issue.msg === 'string'
    )
      fields[issue.loc[1]] ??= issue.msg.trim();
  }
  return fields;
}

export class ApiError extends Error {
  readonly status: number;
  readonly location: ValidationLocation | null;
  readonly fields: Partial<Record<string, string>>;

  constructor(status: number, body?: unknown) {
    super(serverMessage(body) ?? `Request failed with status ${status}.`);
    this.name = 'ApiError';
    this.status = status;
    this.fields = fieldMessages(body);
    this.location = parseValidationLocation(
      body && typeof body === 'object' && 'location' in body
        ? body.location
        : null
    );
  }
}

export class NetworkError extends Error {
  constructor(cause: unknown) {
    super('Could not reach the server. Check your connection and try again.', {
      cause,
    });
    this.name = 'NetworkError';
  }
}

export const workspaceBase =
  window.location.pathname.match(/^\/courses\/[^/]+(?=\/|$)/)?.[0] ?? '';

export function workspaceUrl(path: string) {
  return workspaceBase + path;
}

function makeClient(baseUrl: string) {
  const instance = createClient<paths>({
    baseUrl,
    fetch: async (request) => {
      try {
        return await globalThis.fetch(request);
      } catch (error) {
        if (request.signal.aborted) throw error;
        if (error instanceof TypeError) throw new NetworkError(error);
        throw error;
      }
    },
  });

  instance.use({
    async onResponse({ request, response }) {
      if (response.ok) return;
      // Decode errors before openapi-fetch parses the body.
      const body: unknown = await response.json().catch((error: unknown) => {
        if (request.signal.aborted) throw error;
        return undefined;
      });
      throw new ApiError(response.status, body);
    },
  });

  return instance;
}

export const client = makeClient(window.location.origin + workspaceBase);
export const serverClient = makeClient(window.location.origin);

/** Require data from a successful client request; bodyless operations skip this. */
export function readData<T>({ data }: { data?: T }): T {
  if (data === undefined)
    throw new Error('The server returned an empty response. Try again.');
  return data;
}

/** POST or PUT a PDF as the raw request body, reporting upload progress. */
export function uploadPdf<T>(
  method: 'POST' | 'PUT',
  path: string,
  file: File,
  progress?: (percent: number) => void
): Promise<T> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open(method, workspaceUrl(path));
    request.setRequestHeader('Content-Type', 'application/pdf');
    request.responseType = 'json';
    request.upload.onprogress = (event) => {
      if (event.lengthComputable && progress)
        progress(Math.round((event.loaded / event.total) * 100));
    };
    request.onerror = () =>
      reject(
        new Error(
          'Upload interrupted. Retry this file; completed uploads are preserved.'
        )
      );
    request.onload = () => {
      if (request.status >= 200 && request.status < 300)
        return resolve(request.response as T);
      reject(new ApiError(request.status, request.response));
    };
    request.send(file);
  });
}
