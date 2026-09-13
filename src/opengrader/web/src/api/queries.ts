import { queryOptions } from '@tanstack/react-query';
import createClient from 'openapi-fetch';
import type { components, paths } from './schema';

export type Assignment = components['schemas']['Assignment'];
export type Answer = components['schemas']['Answer'];
export type Question = components['schemas']['Question'];
export type Submission = components['schemas']['Submission'];

const client = createClient<paths>({
  baseUrl: window.location.origin,
  fetch: (request) => globalThis.fetch(request),
});

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number) {
    super(`Request failed with status ${status}`);
    this.status = status;
  }
}

function readData<T>({ data, response }: { data?: T; response: Response }): T {
  if (!response.ok || data === undefined) throw new ApiError(response.status);
  return data;
}

export const assignmentsQuery = queryOptions({
  queryKey: ['assignments'],
  queryFn: async ({ signal }) =>
    readData(await client.GET('/assignments', { signal })),
});

export function submissionsQuery(assignmentId: string) {
  return queryOptions({
    queryKey: ['assignments', assignmentId, 'submissions'],
    queryFn: async ({ signal }) =>
      readData(
        await client.GET('/assignments/{assignment_id}/submissions', {
          params: { path: { assignment_id: assignmentId } },
          signal,
        })
      ),
  });
}

export function questionsQuery(assignmentId: string, submissionId: number) {
  return queryOptions({
    queryKey: ['assignments', assignmentId, 'questions', submissionId],
    queryFn: async ({ signal }) =>
      readData(
        await client.GET('/assignments/{assignment_id}/questions', {
          params: {
            path: { assignment_id: assignmentId },
            query: { submission_id: submissionId },
          },
          signal,
        })
      ),
  });
}

export function answerQuery(
  assignmentId: string,
  submissionId: number,
  questionId: string
) {
  return queryOptions({
    queryKey: [
      'assignments',
      assignmentId,
      'answers',
      submissionId,
      questionId,
    ],
    queryFn: async ({ signal }) =>
      readData(
        await client.GET(
          '/assignments/{assignment_id}/submissions/{submission_id}/answers/{question_id}',
          {
            params: {
              path: {
                assignment_id: assignmentId,
                submission_id: submissionId,
                question_id: questionId,
              },
            },
            signal,
          }
        )
      ),
    retry: (count, error) =>
      !(error instanceof ApiError && error.status === 404) && count < 1,
  });
}

export type QAPreview = components['schemas']['QAPreview'];

export function assignmentQAQuery(assignmentId: string) {
  return queryOptions({
    queryKey: ['assignments', assignmentId, 'qa'],
    queryFn: async ({ signal }) =>
      readData(
        await client.GET('/assignments/{assignment_id}/qa', {
          params: { path: { assignment_id: assignmentId } },
          signal,
        })
      ),
  });
}

function mutationData<T>({
  data,
  error,
  response,
}: {
  data?: T;
  error?: { detail?: string | components['schemas']['ValidationError'][] };
  response: Response;
}): T {
  if (!response.ok) {
    const detail = error?.detail;
    const message = Array.isArray(detail)
      ? detail.map((issue) => issue.msg).join('\n')
      : detail;
    throw new Error(
      message || `Request failed with status ${response.status}. Try again.`
    );
  }
  return readData({ data, response });
}

export async function previewAssignment(
  qaMarkdown: string,
  signal: AbortSignal
) {
  return mutationData(
    await client.POST('/assignments/preview', {
      body: { qa_markdown: qaMarkdown },
      signal,
    })
  );
}

export async function createAssignment(qaMarkdown: string) {
  return mutationData(
    await client.POST('/assignments', {
      body: { qa_markdown: qaMarkdown },
    })
  );
}

export async function previewAssignmentPdf(
  qaMarkdown: string,
  version: string,
  signal: AbortSignal
) {
  return mutationData(
    await client.POST('/assignments/preview/pdf', {
      body: { qa_markdown: qaMarkdown, version },
      signal,
      parseAs: 'blob',
    })
  );
}

export async function savedAssignmentPdf(
  assignmentId: string,
  version: string,
  signal: AbortSignal
) {
  return mutationData(
    await client.GET('/assignments/{assignment_id}/pdf', {
      params: { path: { assignment_id: assignmentId }, query: { version } },
      signal,
      parseAs: 'blob',
    })
  );
}

export function assignmentSourceQuery(assignmentId: string) {
  return queryOptions({
    queryKey: ['assignments', assignmentId, 'source'],
    queryFn: async ({ signal }) =>
      mutationData(
        await client.GET('/assignments/{assignment_id}/source', {
          params: { path: { assignment_id: assignmentId } },
          signal,
        })
      ),
  });
}

export async function updateAssignment(
  assignmentId: string,
  qaMarkdown: string,
  expectedQaMarkdown: string
) {
  return mutationData(
    await client.PUT('/assignments/{assignment_id}', {
      params: { path: { assignment_id: assignmentId } },
      body: {
        qa_markdown: qaMarkdown,
        expected_qa_markdown: expectedQaMarkdown,
      },
    })
  );
}

export async function deleteAssignment(assignmentId: string) {
  const result = await client.DELETE('/assignments/{assignment_id}', {
    params: { path: { assignment_id: assignmentId } },
  });
  if (!result.response.ok) mutationData(result);
}
