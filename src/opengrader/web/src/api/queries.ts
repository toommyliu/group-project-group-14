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
