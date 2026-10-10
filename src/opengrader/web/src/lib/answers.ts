import type { AnswerField } from '@/api/queries';

/** What an answer box holds while it is edited: letters, or the lines typed. */
export type AnswerDraft = string[];

/** The answers as they are sent: trimmed, blanks dropped. */
export function cleanAnswers(draft: AnswerDraft): string[] {
  return draft.map((line) => line.trim()).filter(Boolean);
}

export function sameAnswers(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((value, index) => value === b[index]);
}

/** The questions of one version that have no answer yet. */
export function unanswered(
  fields: AnswerField[] | undefined,
  version: string | null
): AnswerField[] {
  return (fields ?? []).filter(
    (field) => (field.version ?? null) === version && !field.answers.length
  );
}
