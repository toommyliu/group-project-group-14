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

export class ApiError extends Error {
  readonly status: number;
  readonly location: ValidationLocation | null;

  constructor(status: number, body?: unknown) {
    super(serverMessage(body) ?? `Request failed with status ${status}.`);
    this.name = 'ApiError';
    this.status = status;
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

export const client = createClient<paths>({
  baseUrl: window.location.origin,
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

client.use({
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

/** Require data from a successful client request; bodyless operations skip this. */
export function readData<T>({ data }: { data?: T }): T {
  if (data === undefined)
    throw new Error('The server returned an empty response. Try again.');
  return data;
}
