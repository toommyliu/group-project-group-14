import type { AnswerField, RubricItem, RubricDraftResult } from '@/api/queries';
import { cleanAnswers, sameAnswers, type AnswerDraft } from './answers';

export type RubricEditorDraft = {
  opened: RubricItem[];
  field: AnswerField | null;
  points: number;
  rubric: RubricItem[];
  answers: AnswerDraft;
  aiDraft: RubricDraftResult | null;
  generation:
    | { status: 'idle' }
    | { status: 'running'; controller: AbortController }
    | { status: 'failed'; error: unknown };
};

export function rubricEditorChanged(draft: RubricEditorDraft) {
  return (
    JSON.stringify(draft.rubric) !== JSON.stringify(draft.opened) ||
    (draft.field !== null &&
      !sameAnswers(cleanAnswers(draft.answers), draft.field.answers))
  );
}

// This tab owns unsaved edits and the request, rather than the panel that shows them.
export function rubricEditorQuery(
  assignmentId: string,
  questionId: string,
  version: string | null
) {
  return {
    queryKey: ['rubric-editor', assignmentId, questionId, version],
    enabled: false,
    staleTime: Infinity,
    gcTime: Infinity,
  };
}
