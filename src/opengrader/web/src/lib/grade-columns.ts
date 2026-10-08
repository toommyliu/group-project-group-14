import type { QAPreview } from '@/api/queries';

export type Column = { id: string; label: string; points: number };

/** Graded question columns per version, numbered the way the outline shows them. */
export function versionColumns(qa: QAPreview['document']) {
  const versions = Object.keys(qa.version_questions ?? {});
  const entries: [string, QAPreview['document']['questions']][] =
    versions.length
      ? versions.map((version) => [version, qa.version_questions![version]])
      : [['', qa.questions]];
  return new Map(
    entries.map(([version, questions]) => [
      version,
      questions.flatMap((question, index): Column[] =>
        question.subquestions?.length
          ? question.subquestions.map((part, partIndex) => ({
              id: part.id,
              label: `${index + 1}.${partIndex + 1}`,
              points: part.points,
            }))
          : [
              {
                id: question.id,
                label: `${index + 1}`,
                points: question.points,
              },
            ]
      ),
    ])
  );
}

export function scoreText(score: number | null | undefined) {
  return score === null || score === undefined
    ? ''
    : String(Math.round(score * 100) / 100);
}

export function percentText(total: number, possible: number) {
  return possible ? `${Math.round((total / possible) * 1000) / 10}%` : '';
}
