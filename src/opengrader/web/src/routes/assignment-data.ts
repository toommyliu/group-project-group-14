import { ApiError } from '@/api/client';
import type { QueryClient } from '@tanstack/react-query';
import {
  answerQuery,
  assignmentsQuery,
  questionsQuery,
  submissionsQuery,
  type Assignment,
} from '@/api/queries';
import { assignmentNotFound, submissionNotFound } from '@/lib/not-found';

export type GradingSearch = { submission?: number; question?: string };

export function validateGradingSearch(
  search: Record<string, unknown>
): GradingSearch {
  return {
    submission:
      typeof search.submission === 'number' &&
      Number.isSafeInteger(search.submission) &&
      search.submission > 0
        ? search.submission
        : undefined,
    question:
      typeof search.question === 'string' && search.question.length > 0
        ? search.question
        : undefined,
  };
}

export async function requireAssignment(
  client: QueryClient,
  assignmentId: string
) {
  const find = (assignments: Assignment[]) =>
    assignments.find((item) => item.id === assignmentId);
  const cached = client.getQueryData(assignmentsQuery.queryKey);
  const assignment =
    (cached && find(cached)) ??
    find(await client.fetchQuery({ ...assignmentsQuery, staleTime: 0 }));
  if (!assignment) throw assignmentNotFound();
  return assignment;
}

/** Load a complete grading selection before replacing the visible response. */
export async function loadAssignment(
  client: QueryClient,
  assignmentId: string,
  search: GradingSearch
) {
  const assignment = await requireAssignment(client, assignmentId);
  const submissions = await client.ensureQueryData(
    submissionsQuery(assignmentId)
  );
  const submission =
    search.submission === undefined
      ? submissions[0]
      : submissions.find((item) => item.id === search.submission);
  if (search.submission !== undefined && !submission)
    throw submissionNotFound();
  const questions = submission
    ? await client.ensureQueryData(questionsQuery(assignmentId, submission.id))
    : [];
  const question =
    questions.find((item) => item.id === search.question) ?? questions[0];
  let answer = null;
  if (submission && question) {
    try {
      answer = await client.fetchQuery({
        ...answerQuery(assignmentId, submission.id, question.id),
        staleTime: 0,
      });
    } catch (error) {
      if (!(error instanceof ApiError && error.status === 404)) throw error;
    }
  }
  return { assignment, submissions, submission, questions, question, answer };
}
