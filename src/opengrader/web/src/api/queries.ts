import { queryOptions } from '@tanstack/react-query';
import { ApiError, client, readData } from './client';
import type { components } from './schema';

export type Assignment = components['schemas']['Assignment'];
export type Answer = components['schemas']['Answer'];
export type Question = components['schemas']['Question'];
export type Submission = components['schemas']['Submission'];
export type FeedbackJob = components['schemas']['FeedbackJob'];
export type ScoreStatistics = components['schemas']['QuestionStatistics'];
export type AssignmentVersionStatistics =
  components['schemas']['AssignmentVersionStatistics'];

export function assignmentStatisticsQuery(assignmentId: string) {
  return queryOptions({
    queryKey: ['assignments', assignmentId, 'statistics'],
    queryFn: async ({ signal }) =>
      readData(
        await client.GET('/api/assignments/{assignment_id}/statistics', {
          params: { path: { assignment_id: assignmentId } },
          signal,
        })
      ),
  });
}

export const feedbackJobsQuery = queryOptions({
  queryKey: ['feedback-jobs'],
  queryFn: async () => readData(await client.GET('/api/jobs')),
});

export async function retryFeedback(
  assignmentId: string,
  submissionId: number
) {
  await client.POST('/api/jobs/{assignment_id}/{submission_id}/retry', {
    params: {
      path: { assignment_id: assignmentId, submission_id: submissionId },
    },
  });
}

export async function downloadFeedback(
  assignmentId: string,
  submissionIds: number[]
) {
  return readData(
    await client.GET('/api/assignments/{assignment_id}/feedback', {
      params: {
        path: { assignment_id: assignmentId },
        query: { submission: submissionIds },
      },
      parseAs: 'blob',
    })
  );
}

export type AssignmentSummary = components['schemas']['AssignmentSummary'];

export const assignmentSummariesQuery = queryOptions({
  queryKey: ['assignment-summaries'],
  queryFn: async ({ signal }) =>
    readData(await client.GET('/api/assignment-summaries', { signal })),
});

export const assignmentsQuery = queryOptions({
  queryKey: ['assignments'],
  // Navigation and route loaders share this request across observer unmounts.
  queryFn: async () => readData(await client.GET('/api/assignments')),
});

export function submissionsQuery(assignmentId: string) {
  return queryOptions({
    queryKey: ['assignments', assignmentId, 'submissions'],
    queryFn: async ({ signal }) =>
      readData(
        await client.GET('/api/assignments/{assignment_id}/submissions', {
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
        await client.GET(
          '/api/assignments/{assignment_id}/submissions/{submission_id}/questions',
          {
            params: {
              path: {
                assignment_id: assignmentId,
                submission_id: submissionId,
              },
            },
            signal,
          }
        )
      ),
  });
}

export function submissionAnswersQuery(
  assignmentId: string,
  submissionId: number
) {
  return queryOptions({
    queryKey: ['assignments', assignmentId, 'answers', submissionId],
    queryFn: async ({ signal }) =>
      readData(
        await client.GET(
          '/api/assignments/{assignment_id}/submissions/{submission_id}/answers',
          {
            params: {
              path: {
                assignment_id: assignmentId,
                submission_id: submissionId,
              },
            },
            signal,
          }
        )
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
          '/api/assignments/{assignment_id}/submissions/{submission_id}/answers/{question_id}',
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
    await client.POST('/api/drafts/edit', {
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
        await client.GET('/api/assignments/{assignment_id}/qa', {
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
    await client.POST('/api/drafts/preview', {
      body: { qa_markdown: qaMarkdown },
      signal,
    })
  );
}

export async function createAssignment(qaMarkdown: string) {
  return readData(
    await client.POST('/api/assignments', {
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
    await client.POST('/api/drafts/pdf', {
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
    await client.GET('/api/assignments/{assignment_id}/pdf', {
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
        await client.GET('/api/assignments/{assignment_id}/source', {
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
    await client.PUT('/api/assignments/{assignment_id}', {
      params: { path: { assignment_id: assignmentId } },
      body: {
        qa_markdown: qaMarkdown,
        expected_qa_markdown: expectedQaMarkdown,
      },
    })
  );
}

export type RubricItem = components['schemas']['QARubricItem-Input'];
export type RubricEditResult = components['schemas']['RubricEditResult'];

/** Save one question's rubric into the class folder's answer key (a folder session). */
export async function saveRubric(
  assignmentId: string,
  questionId: string,
  body: components['schemas']['RubricEdit']
) {
  return readData(
    await client.PUT(
      '/api/assignments/{assignment_id}/questions/{question_id}/rubric',
      {
        params: {
          path: { assignment_id: assignmentId, question_id: questionId },
        },
        body,
      }
    )
  );
}

export async function deleteAssignment(assignmentId: string) {
  await client.DELETE('/api/assignments/{assignment_id}', {
    params: { path: { assignment_id: assignmentId } },
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
      '/api/assignments/{assignment_id}/submissions/{submission_id}/answers/{question_id}',
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
