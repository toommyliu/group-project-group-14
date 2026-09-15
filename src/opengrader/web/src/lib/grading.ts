import type { Question, Submission } from '@/api/queries';

export function selectRubric(
  question: Question,
  labels: string[],
  index: number
) {
  const option = question.rubric[index];
  if (!option) return labels;
  const keep = labels.filter((label) => {
    const item = question.rubric.find((candidate) => candidate.label === label);
    if (!item) return false;
    if (option.kind === 'bonus') return true;
    if (item.kind === 'bonus') return true;
    return (
      option.selection_mode === 'checklist' &&
      item.selection_mode === 'checklist'
    );
  });
  if (option.kind !== 'bonus' && option.selection_mode === 'exclusive') {
    return [...keep, option.label];
  }
  return keep.includes(option.label)
    ? keep.filter((label) => label !== option.label)
    : [...keep, option.label];
}

/** Preview the shared Python rubric rules; the server calculates the saved score. */
export function rubricScore(question: Question, labels: string[]) {
  const selected = question.rubric.filter((option) =>
    labels.includes(option.label)
  );
  if (!selected.length) return null;
  const base = question.rubric.filter((option) => option.kind !== 'bonus');
  const positive =
    base.some((option) => option.points_delta > 1e-6) &&
    !base.some((option) => option.points_delta < -1e-6);
  const total = selected
    .filter((option) => option.kind !== 'bonus')
    .reduce(
      (sum, option) => sum + option.points_delta,
      positive ? 0 : question.possible_points
    );
  const bonus = selected
    .filter((option) => option.kind === 'bonus')
    .reduce((sum, option) => sum + option.points_delta, 0);
  return Math.max(0, Math.min(total, question.possible_points)) + bonus;
}

/** Wrap once and skip missing responses, which cannot be graded. */
export function nextUngraded(
  submissions: Submission[],
  currentId: number,
  questionId: string
) {
  const index = submissions.findIndex((item) => item.id === currentId);
  const remaining = [
    ...submissions.slice(index + 1),
    ...submissions.slice(0, index),
  ];
  return remaining.find((item) => item.question_scores[questionId] === null);
}
