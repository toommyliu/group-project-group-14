import { QueryClient } from '@tanstack/react-query';
import { afterEach, beforeAll, expect, it, vi } from 'vitest';

let api: typeof import('./client');
let queries: typeof import('./queries');

beforeAll(async () => {
  vi.stubGlobal('window', {
    location: { origin: 'http://localhost', pathname: '/' },
  });
  api = await import('./client');
  queries = await import('./queries');
});

afterEach(() => vi.unstubAllGlobals());

it('preserves HTTP status and server detail through a query', async () => {
  vi.stubGlobal(
    'fetch',
    vi
      .fn()
      .mockResolvedValue(
        Response.json({ detail: 'Assignment not found' }, { status: 404 })
      )
  );
  const cache = new QueryClient();
  await expect(
    cache.fetchQuery(queries.answerQuery('missing', 1, 'q1'))
  ).rejects.toMatchObject({
    name: 'ApiError',
    status: 404,
    message: 'Assignment not found',
  });
  expect(fetch).toHaveBeenCalledTimes(1);
  cache.clear();
});

it('preserves the validation target returned by the parser', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      Response.json(
        {
          detail:
            "question 'q1' rubric item 'Correct' must stay within -0 to +0 points",
          location: {
            line: 9,
            version: 'B',
            question_path: [0],
            rubric_path: [0],
          },
        },
        { status: 422 }
      )
    )
  );
  await expect(
    queries.previewAssignment('invalid rubric', new AbortController().signal)
  ).rejects.toMatchObject({
    status: 422,
    location: { line: 9, version: 'B', question_path: [0], rubric_path: [0] },
  });
});

it('formats validation details identically for assignment mutations', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation(async () =>
      Response.json(
        {
          detail: [
            { msg: 'Choose an assignment.' },
            { msg: 'Enter a title.' },
            null,
            { msg: 3 },
          ],
        },
        { status: 422 }
      )
    )
  );
  for (const operation of [
    () => queries.createAssignment(''),
    () => queries.updateAssignment('a', '', ''),
  ]) {
    await expect(operation()).rejects.toMatchObject({
      name: 'ApiError',
      status: 422,
      message: 'Choose an assignment.\nEnter a title.',
    });
  }
});

it.each(['<html>Bad gateway</html>', '{invalid json', '{"detail":"  "}'])(
  'keeps HTTP status when the response has no usable detail: %s',
  async (body) => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response(body, { status: 502 }))
    );
    await expect(queries.createAssignment('')).rejects.toMatchObject({
      name: 'ApiError',
      status: 502,
      message: expect.stringContaining('502'),
    });
  }
);

it('decodes error details on PDF requests without changing successful blobs', async () => {
  vi.stubGlobal(
    'fetch',
    vi
      .fn()
      .mockResolvedValueOnce(
        Response.json(
          { detail: 'Choose an available version.' },
          { status: 422 }
        )
      )
      .mockResolvedValueOnce(
        new Response('pdf bytes', {
          headers: { 'Content-Type': 'application/pdf' },
        })
      )
  );
  const signal = new AbortController().signal;
  await expect(
    queries.savedAssignmentPdf('a', 'invalid', signal)
  ).rejects.toMatchObject({
    status: 422,
    message: 'Choose an available version.',
  });
  const pdf = await queries.savedAssignmentPdf('a', 'A', signal);
  expect(pdf.type).toBe('application/pdf');
  expect(await pdf.text()).toBe('pdf bytes');
});

it('accepts bodyless deletes but rejects missing required data', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation(async () => new Response(null, { status: 204 }))
  );
  await expect(queries.deleteAssignment('a')).resolves.toBeUndefined();
  await expect(queries.createAssignment('')).rejects.toThrow('empty response');
});

it('identifies fetch failures and retains their cause', async () => {
  const cause = new TypeError('Failed to fetch');
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(cause));
  await expect(queries.createAssignment('')).rejects.toMatchObject({
    name: 'NetworkError',
    cause,
  });
});

it('preserves cancellation even when a custom abort reason is a TypeError', async () => {
  const controller = new AbortController();
  const reason = new TypeError('Preview replaced');
  const started = new Promise<void>((resolve) => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (request: Request) =>
          new Promise<Response>((_, reject) => {
            request.signal.addEventListener(
              'abort',
              () => reject(request.signal.reason),
              { once: true }
            );
            resolve();
          })
      )
    );
  });
  const result = queries.previewAssignment('', controller.signal);
  const assertion = expect(result).rejects.toBe(reason);
  await started;
  controller.abort(reason);
  await assertion;
});

it('does not classify response parsing bugs as connection failures', async () => {
  const response = Response.json({});
  const cause = new TypeError('Decoder failed');
  vi.spyOn(response, 'text').mockRejectedValue(cause);
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response));
  await expect(queries.createAssignment('')).rejects.toBe(cause);
  expect(cause).not.toBeInstanceOf(api.NetworkError);
});
