import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { TriangleAlert } from 'lucide-react';
import {
  answerFieldsQuery,
  assignmentQAQuery,
  draftRubric,
  planRubricDraft,
  saveRubric,
  type AnswerField,
  type QAPreview,
  type RubricItem,
} from '@/api/queries';
import { ApiError } from '@/api/client';
import { useFreshSinceOpen } from '@/hooks/use-fresh-since-open';
import { useFolderSession } from '@/hooks/use-folder-session';
import { cleanAnswers, sameAnswers, type AnswerDraft } from '@/lib/answers';
import {
  rubricEditorQuery,
  rubricEditorChanged,
  type RubricEditorDraft,
} from '@/lib/rubric-editor-draft';
import { AnswersInput } from './answers-input';
import { RegradeNote } from './regrade-note';
import { ErrorAlert } from './error-alert';
import { RubricEditor } from './rubric-editor';
import { Button } from './ui/button';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from './ui/alert-dialog';
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

export function RubricEditSheet({
  assignmentId,
  target,
  onClose,
}: {
  assignmentId: string;
  target: RubricTarget | null;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const folderSession = useFolderSession();
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
  const [reloadVersion, setReloadVersion] = useState(0);
  const reload = async () => {
    const results = await Promise.all([qa.refetch(), fields.refetch()]);
    if (results.every((result) => result.isSuccess)) {
      if (target)
        client.removeQueries({
          queryKey: rubricEditorQuery(
            assignmentId,
            target.questionId,
            target.version
          ).queryKey,
          exact: true,
        });
      setReloadVersion((version) => version + 1);
    }
  };
  // Kept here so a completed save can replace the form with its outcome.
  const [saved, setSaved] = useState<Saved | null>(null);
  const close = () => {
    if (target) {
      const key = rubricEditorQuery(
        assignmentId,
        target.questionId,
        target.version
      ).queryKey;
      const draft = client.getQueryData<RubricEditorDraft>(key);
      if (
        draft?.generation.status === 'idle' &&
        !draft.aiDraft &&
        !rubricEditorChanged(draft)
      )
        client.removeQueries({ queryKey: key, exact: true });
    }
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
            {folderSession ? 'Edit answers and rubric' : 'Edit rubric'}
            {target ? ` · Question ${target.questionId}` : ''}
          </SheetTitle>
          <SheetDescription className="text-sm">
            {folderSession
              ? 'Saved into the answer key file in the class folder. '
              : 'Saved in this workspace. '}
            Grades follow rubric items you rename or re-score. An item that
            saved grades use cannot be removed.
          </SheetDescription>
        </SheetHeader>
        {target === null ? null : saved ? (
          <SavedNote
            assignmentId={assignmentId}
            saved={saved}
            onClose={close}
          />
        ) : failed && !fresh ? (
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
            // Only an explicit reload replaces a draft; background events
            // also refresh these queries while another grader is working.
            key={`${target.questionId}/${target.version ?? ''}/${reloadVersion}`}
            assignmentId={assignmentId}
            target={target}
            opened={found.rubric}
            field={field}
            points={found.points}
            editAnswers={!!folderSession}
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
  opened: initialOpened,
  field: initialField,
  points: initialPoints,
  editAnswers,
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
  editAnswers: boolean;
  reloading: boolean;
  onReload: () => void;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const editorQuery = rubricEditorQuery(
    assignmentId,
    target.questionId,
    target.version
  );
  const { data: draft } = useQuery<RubricEditorDraft>({
    ...editorQuery,
    initialData: (): RubricEditorDraft => ({
      opened: initialOpened,
      field: initialField,
      points: initialPoints,
      rubric: initialOpened,
      answers: initialField?.answers ?? [],
      aiDraft: null,
      generation: { status: 'idle' as const },
    }),
  });
  const { opened, field, points, rubric, answers, aiDraft, generation } = draft;
  const setDraft = (patch: Partial<RubricEditorDraft>) =>
    client.setQueryData<RubricEditorDraft>(editorQuery.queryKey, (current) =>
      current ? { ...current, ...patch } : current
    );
  const setRubric = (rubric: RubricItem[]) => setDraft({ rubric });
  const setAnswers = (answers: AnswerDraft) => setDraft({ answers });
  const openedAnswers = field?.answers ?? [];
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const drafting = generation.status === 'running';
  const draftError = generation.status === 'failed' ? generation.error : null;
  const [confirm, setConfirm] = useState<string | null>(null);
  const answersChanged =
    field !== null && !sameAnswers(cleanAnswers(answers), openedAnswers);
  const changed = rubricEditorChanged(draft);
  // The file moved on underneath this form: only a reload can continue.
  const stale = error instanceof ApiError && error.status === 409;

  async function generate(sendToCloud = false) {
    if (drafting) return;
    const controller = new AbortController();
    setDraft({ generation: { status: 'running', controller } });
    // A cancelled request must not replace a later draft, even if its reply arrives.
    const finish = (patch: Partial<RubricEditorDraft>) =>
      client.setQueryData<RubricEditorDraft>(editorQuery.queryKey, (current) =>
        current?.generation.status === 'running' &&
        current.generation.controller === controller
          ? { ...current, generation: { status: 'idle' as const }, ...patch }
          : current
      );
    setConfirm(null);
    try {
      if (!sendToCloud) {
        const plan = await planRubricDraft(
          assignmentId,
          target.questionId,
          target.version,
          controller.signal
        );
        if (plan.confirm) {
          setConfirm(plan.confirm);
          finish({});
          return;
        }
      }
      const result = await draftRubric(
        assignmentId,
        target.questionId,
        {
          version: target.version,
          expected_answers: openedAnswers,
          ...(editAnswers ? { answers: cleanAnswers(answers) } : {}),
          send_to_cloud: sendToCloud,
        },
        controller.signal
      );
      finish({ rubric: result.rubric, aiDraft: result });
    } catch (cause) {
      if (!controller.signal.aborted)
        finish({ generation: { status: 'failed', error: cause } });
    }
  }

  function cancelGeneration() {
    if (generation.status === 'running') generation.controller.abort();
    setDraft({ generation: { status: 'idle' } });
  }

  function discard() {
    cancelGeneration();
    client.removeQueries({ queryKey: editorQuery.queryKey, exact: true });
    onClose();
  }

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const result = await saveRubric(assignmentId, target.questionId, {
        version: target.version,
        rubric,
        expected_rubric: opened,
        ...(answersChanged
          ? { answers: cleanAnswers(answers), expected_answers: openedAnswers }
          : aiDraft
            ? { expected_answers: openedAnswers }
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
      client.removeQueries({ queryKey: editorQuery.queryKey, exact: true });
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
        {editAnswers && field && (
          <section className="grid gap-2" aria-label="Answers">
            <h3 className="font-medium">
              {field.choices.length ? 'Correct option' : 'Accepted answers'}
            </h3>
            <AnswersInput
              field={field}
              value={answers}
              disabled={saving || reloading || drafting}
              onChange={setAnswers}
            />
            <p className="text-muted-foreground">
              {field.choices.length
                ? 'Tick the correct option, or every correct one.'
                : 'One per line. Leave it empty to grade this question by hand.'}
            </p>
          </section>
        )}
        {field && field.choices.length === 0 && (
          <div className="grid gap-2">
            <div>
              <Button
                variant="outline"
                disabled={!drafting && (saving || reloading || stale)}
                onClick={() =>
                  drafting ? cancelGeneration() : void generate()
                }
              >
                {drafting ? 'Cancel AI' : 'Draft with AI'}
              </Button>
            </div>
            <p role="status" className="text-muted-foreground">
              {drafting
                ? 'Reading student answers and drafting partial credit. You can close this panel while it runs.'
                : aiDraft
                  ? `AI draft from ${aiDraft.sample_count} distinct ${aiDraft.sample_count === 1 ? 'answer' : 'answers'} across ${aiDraft.student_count} ${aiDraft.student_count === 1 ? 'student' : 'students'}. Review and edit, then Save.`
                  : ''}
            </p>
            {aiDraft?.rationale && <p>{aiDraft.rationale}</p>}
            {draftError !== null && (
              <ErrorAlert
                error={draftError}
                fallback="Could not draft the rubric. Try again."
              />
            )}
          </div>
        )}
        <RubricEditor
          rubric={rubric}
          points={points}
          disabled={saving || reloading || drafting}
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
        {(changed || aiDraft || draftError !== null) && !drafting && (
          <Button variant="ghost" disabled={saving} onClick={discard}>
            Discard draft
          </Button>
        )}
        <Button variant="outline" disabled={saving} onClick={onClose}>
          Close
        </Button>
        <Button
          disabled={!changed || saving || reloading || drafting || stale}
          onClick={() => void save()}
        >
          {saving ? 'Saving…' : 'Save'}
        </Button>
      </SheetFooter>
      <AlertDialog
        open={confirm !== null}
        onOpenChange={(open) => !open && setConfirm(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Send answers to a cloud AI?</AlertDialogTitle>
            <AlertDialogDescription className="whitespace-pre-wrap">
              {confirm}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Don't send</AlertDialogCancel>
            <AlertDialogAction onClick={() => void generate(true)}>
              Send and draft
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
