import { queryOptions } from '@tanstack/react-query';
import { ApiError, client, readData } from './client';
import type { components } from './schema';

export type Assignment = components['schemas']['Assignment'];
export type Answer = components['schemas']['Answer'];
export type Question = components['schemas']['Question'];
export type Submission = components['schemas']['Submission'];

export const assignmentsQuery = queryOptions({
  queryKey: ['assignments'],
  // Navigation and route loaders share this request across observer unmounts.
  queryFn: async () => readData(await client.GET('/assignments')),
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
export type QuestionEdit = components['schemas']['QuestionEdit'];
export type VisualEditResult = components['schemas']['VisualEditResult'];
export type DocumentEdit = components['schemas']['DocumentEdit'];

export async function editAssignmentDraft(
  qaMarkdown: string,
  edits: QuestionEdit[],
  metadata: DocumentEdit,
  signal: AbortSignal
) {
  return readData(
    await client.POST('/assignments/preview/edit', {
      body: { qa_markdown: qaMarkdown, edits, metadata },
      signal,
    })
  );
}

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

export async function previewAssignment(
  qaMarkdown: string,
  signal: AbortSignal
) {
  return readData(
    await client.POST('/assignments/preview', {
      body: { qa_markdown: qaMarkdown },
      signal,
    })
  );
}

export async function createAssignment(qaMarkdown: string) {
  return readData(
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
  return readData(
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
  return readData(
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
      readData(
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
  return readData(
    await client.PUT('/assignments/{assignment_id}', {
      params: { path: { assignment_id: assignmentId } },
      body: {
        qa_markdown: qaMarkdown,
        expected_qa_markdown: expectedQaMarkdown,
      },
    })
  );
}

export async function deleteAssignment(
  assignmentId: string,
  stopTerminalSessions: boolean
) {
  await client.DELETE('/assignments/{assignment_id}', {
    params: {
      path: { assignment_id: assignmentId },
      query: { stop_terminal_sessions: stopTerminalSessions },
    },
  });
}

export type GradeEdit = components['schemas']['GradeEdit'];

export async function saveAnswer(
  assignmentId: string,
  submissionId: number,
  questionId: string,
  body: GradeEdit
) {
  return readData(
    await client.PUT(
      '/assignments/{assignment_id}/submissions/{submission_id}/answers/{question_id}',
      {
        params: {
          path: {
            assignment_id: assignmentId,
            submission_id: submissionId,
            question_id: questionId,
          },
        },
        body,
      }
    )
  );
}
