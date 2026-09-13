import type { QueryClient } from '@tanstack/react-query';
import {
  answerQuery,
  ApiError,
  assignmentsQuery,
  questionsQuery,
  submissionsQuery,
} from '@/api/queries';

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

/** Load a complete grading selection before replacing the visible response. */
export async function loadAssignment(
  client: QueryClient,
  assignmentId: string,
  search: GradingSearch
) {
  const assignments = await client.ensureQueryData(assignmentsQuery);
  const assignment = assignments.find((item) => item.id === assignmentId);
  if (!assignment) return null;
  const submissions = await client.ensureQueryData(
    submissionsQuery(assignmentId)
  );
  const submission =
    search.submission === undefined
      ? submissions[0]
      : submissions.find((item) => item.id === search.submission);
  if (search.submission !== undefined && !submission) return null;
  const questions = submission
    ? await client.ensureQueryData(questionsQuery(assignmentId, submission.id))
    : [];
  const question =
    questions.find((item) => item.id === search.question) ?? questions[0];
  let answer = null;
  if (submission && question) {
    try {
      answer = await client.ensureQueryData(
        answerQuery(assignmentId, submission.id, question.id)
      );
    } catch (error) {
      if (!(error instanceof ApiError && error.status === 404)) throw error;
    }
  }
  return { assignment, submissions, submission, questions, question, answer };
}
