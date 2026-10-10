import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { TriangleAlert } from 'lucide-react';
import {
  answerFieldsQuery,
  assignmentQAQuery,
  saveRubric,
  type AnswerField,
  type QAPreview,
  type RubricItem,
} from '@/api/queries';
import { ApiError } from '@/api/client';
import { useFreshSinceOpen } from '@/hooks/use-fresh-since-open';
import { cleanAnswers, sameAnswers, type AnswerDraft } from '@/lib/answers';
import { AnswersInput } from './answers-input';
import { RegradeNote } from './regrade-note';
import { ErrorAlert } from './error-alert';
import { RubricEditor } from './rubric-editor';
import { Button } from './ui/button';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from './ui/sheet';

/** The question whose rubric is being edited; `version` is null without versions. */
export type RubricTarget = { questionId: string; version: string | null };

type Found = { rubric: RubricItem[]; points: number; parts: boolean };

/** What a save has to tell: grades it could not write yet, and whether answers changed. */
type Saved = { problems: string[]; answers: boolean };

function findQuestion(
  { document }: QAPreview,
  { questionId, version }: RubricTarget
): Found | null {
  const questions =
    version && document.version_questions?.[version]
      ? document.version_questions[version]
      : document.questions;
  for (const parent of questions ?? []) {
    if (parent.id === questionId)
      return {
        rubric: parent.rubric ?? [],
        points: parent.points,
        parts: Boolean(parent.subquestions?.length),
      };
    for (const part of parent.subquestions ?? [])
      if (part.id === questionId)
        return { rubric: part.rubric ?? [], points: part.points, parts: false };
  }
  return null;
}

/**
 * Edit one question's answers and rubric while grading, in a folder session.
 *
 * It opens on the question as the answer key file has it now and saves
 * straight back into that file, so there is no draft to lose and nothing to
 * "send" afterwards. Answers and rubric go in one write: never one without
 * the other. The server refuses the save if either changed underneath it or
 * if the rubric drops an item that saved grades still use.
 */
export function RubricEditSheet({
  assignmentId,
  target,
  onClose,
}: {
  assignmentId: string;
  target: RubricTarget | null;
  onClose: () => void;
}) {
  // Always re-read on opening: the terminal app may have edited the file.
  const qa = useQuery({
    ...assignmentQAQuery(assignmentId),
    enabled: target !== null,
    staleTime: 0,
    refetchOnMount: 'always',
  });
  const fields = useQuery({
    ...answerFieldsQuery(assignmentId),
    enabled: target !== null,
    staleTime: 0,
    refetchOnMount: 'always',
  });
  const found = target && qa.data ? findQuestion(qa.data, target) : null;
  const field =
    (target &&
      fields.data?.find(
        (item) =>
          item.question_id === target.questionId &&
          (item.version ?? null) === (target.version || null)
      )) ||
    null;
  const failed = qa.error ?? fields.error;
  // Not the cached copy: the form waits for this opening's own read.
  const fresh = useFreshSinceOpen(target !== null, [
    qa.dataUpdatedAt,
    fields.dataUpdatedAt,
  ]);
  const reload = () => {
    void qa.refetch();
    void fields.refetch();
  };
  // Kept here, not in the form: a save re-reads the file, and the form is
  // rebuilt on every fresh read.
  const [saved, setSaved] = useState<Saved | null>(null);
  const close = () => {
    setSaved(null);
    onClose();
  };
  return (
    <Sheet
      open={target !== null}
      onOpenChange={(open) => {
        if (!open) close();
      }}
    >
      <SheetContent className="gap-0 overflow-y-auto text-sm data-[side=right]:w-full data-[side=right]:sm:max-w-2xl">
        <SheetHeader>
          <SheetTitle className="text-base">
            Edit answers and rubric
            {target ? ` · Question ${target.questionId}` : ''}
          </SheetTitle>
          <SheetDescription className="text-sm">
            Saved straight into the answer key file in the class folder. Grades
            that already use a rubric item you rename or re-score follow it. An
            item that saved grades use cannot be removed.
          </SheetDescription>
        </SheetHeader>
        {target === null ? null : saved ? (
          <SavedNote
            assignmentId={assignmentId}
            saved={saved}
            onClose={close}
          />
        ) : failed ? (
          <div className="px-4">
            <ErrorAlert error={failed} title="Could not load the question">
              <Button variant="outline" onClick={reload}>
                Try again
              </Button>
            </ErrorAlert>
          </div>
        ) : !fresh ? (
          <p className="px-4 text-muted-foreground">Loading the question…</p>
        ) : !found ? (
          <p className="px-4">
            Question {target.questionId} is not in the answer key any more.
          </p>
        ) : found.parts ? (
          <p className="px-4">
            Question {target.questionId} is graded by its parts. Edit the rubric
            of a part instead.
          </p>
        ) : (
          <RubricForm
            // A fresh read of the file is a fresh form, never a merge.
            key={`${target.questionId}/${target.version ?? ''}/${qa.dataUpdatedAt}/${fields.dataUpdatedAt}`}
            assignmentId={assignmentId}
            target={target}
            opened={found.rubric}
            field={field}
            points={found.points}
            reloading={qa.isFetching || fields.isFetching}
            onReload={reload}
            onSaved={setSaved}
            onClose={close}
          />
        )}
      </SheetContent>
    </Sheet>
  );
}

function SavedNote({
  assignmentId,
  saved,
  onClose,
}: {
  assignmentId: string;
  saved: Saved;
  onClose: () => void;
}) {
  return (
    <>
      <div className="grid gap-3 px-4">
        {saved.problems.length ? (
          <>
            <p className="flex items-start gap-2">
              <TriangleAlert
                className="mt-0.5 size-4 shrink-0"
                aria-hidden="true"
              />
              <span>
                The rubric is saved in the answer key. Some grades that use it
                could not be written into their files yet; they are saved here
                and are written the next time this page syncs with the folder.
              </span>
            </p>
            <ul className="list-disc space-y-1 ps-9 text-muted-foreground">
              {saved.problems.map((problem, index) => (
                <li key={index} className="wrap-anywhere">
                  {problem}
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p>Saved in the answer key.</p>
        )}
        {saved.answers && <RegradeNote assignmentId={assignmentId} />}
      </div>
      <SheetFooter>
        <Button onClick={onClose}>Done</Button>
      </SheetFooter>
    </>
  );
}

function RubricForm({
  assignmentId,
  target,
  opened,
  field,
  points,
  reloading,
  onReload,
  onSaved,
  onClose,
}: {
  assignmentId: string;
  target: RubricTarget;
  /** Told what a save left to say; the sheet shows it and outlives this form. */
  onSaved: (outcome: Saved) => void;
  opened: RubricItem[];
  /** The question's answers and options; null if it could not be found among them. */
  field: AnswerField | null;
  points: number;
  reloading: boolean;
  onReload: () => void;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const [rubric, setRubric] = useState(opened);
  const openedAnswers = field?.answers ?? [];
  const [answers, setAnswers] = useState<AnswerDraft>(openedAnswers);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const rubricChanged = JSON.stringify(rubric) !== JSON.stringify(opened);
  const answersChanged =
    field !== null && !sameAnswers(cleanAnswers(answers), openedAnswers);
  const changed = rubricChanged || answersChanged;
  // The file moved on underneath this form: only a reload can continue.
  const stale = error instanceof ApiError && error.status === 409;

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const result = await saveRubric(assignmentId, target.questionId, {
        version: target.version,
        rubric,
        expected_rubric: opened,
        // Left out when untouched, so a rubric-only save cannot be refused
        // over answers nobody was editing.
        ...(answersChanged
          ? { answers: cleanAnswers(answers), expected_answers: openedAnswers }
          : {}),
      });
      // Every answer to this question now carries the new rubric. The pages
      // that show it read it through these queries, so this is all the
      // refresh there is — and the save has already succeeded, so a refresh
      // that fails (the server's own change notice may be refetching the
      // same data at this moment) is not reported as a failed save.
      await client
        .invalidateQueries({ queryKey: ['assignments', assignmentId] })
        .catch(() => undefined);
      if (result.problems.length || answersChanged)
        onSaved({ problems: result.problems, answers: answersChanged });
      else onClose();
    } catch (cause) {
      setError(cause);
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <div className="grid gap-4 px-4">
        {field && (
          <section className="grid gap-2" aria-label="Answers">
            <h3 className="font-medium">
              {field.choices.length ? 'Correct option' : 'Accepted answers'}
            </h3>
            <AnswersInput
              field={field}
              value={answers}
              disabled={saving || reloading}
              onChange={setAnswers}
            />
            <p className="text-muted-foreground">
              {field.choices.length
                ? 'Tick the correct option, or every correct one.'
                : 'One per line. Leave it empty to grade this question by hand.'}
            </p>
          </section>
        )}
        <RubricEditor
          rubric={rubric}
          points={points}
          disabled={saving || reloading}
          onChange={setRubric}
        />
        {error !== null && (
          <ErrorAlert error={error} fallback="Could not save the question.">
            {stale && (
              <Button variant="outline" disabled={reloading} onClick={onReload}>
                Load the current question
              </Button>
            )}
          </ErrorAlert>
        )}
      </div>
      <SheetFooter className="flex-row justify-end">
        <Button variant="outline" disabled={saving} onClick={onClose}>
          Cancel
        </Button>
        <Button
          disabled={!changed || saving || reloading || stale}
          onClick={() => void save()}
        >
          {saving ? 'Saving…' : 'Save'}
        </Button>
      </SheetFooter>
    </>
  );
}
