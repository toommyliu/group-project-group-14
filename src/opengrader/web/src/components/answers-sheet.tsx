import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  answerFieldsQuery,
  saveAnswers,
  type AnswerField,
} from '@/api/queries';
import { ApiError } from '@/api/client';
import { useFreshSinceOpen } from '@/hooks/use-fresh-since-open';
import { cleanAnswers, unanswered, type AnswerDraft } from '@/lib/answers';
import { AnswersInput } from './answers-input';
import { ErrorAlert } from './error-alert';
import { RegradeNote } from './regrade-note';
import { Button } from './ui/button';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from './ui/sheet';

/**
 * Add the answers an answer key is missing, all on one page, in a folder
 * session. A key drafted from the blank template has every question and no
 * answers; this is the quick way through them. Saved in one write into the
 * answer key file, so either all of them are saved or none.
 */
export function AnswersSheet({
  assignmentId,
  version,
  open,
  onClose,
}: {
  assignmentId: string;
  version: string | null;
  open: boolean;
  onClose: () => void;
}) {
  // Always re-read on opening: the terminal app may have edited the file.
  const fields = useQuery({
    ...answerFieldsQuery(assignmentId),
    enabled: open,
    staleTime: 0,
    refetchOnMount: 'always',
  });
  const missing = unanswered(fields.data, version);
  // Not the cached copy: the form waits for this opening's own read.
  const fresh = useFreshSinceOpen(open, [fields.dataUpdatedAt]);
  // Kept here, not in the form: a save re-reads the file, and the form is
  // rebuilt on every fresh read.
  const [saved, setSaved] = useState<number | null>(null);
  const close = () => {
    setSaved(null);
    onClose();
  };
  return (
    <Sheet
      open={open}
      onOpenChange={(next) => {
        if (!next) close();
      }}
    >
      <SheetContent className="gap-0 overflow-y-auto text-sm data-[side=right]:w-full data-[side=right]:sm:max-w-2xl">
        <SheetHeader>
          <SheetTitle className="text-base">Add answers</SheetTitle>
          <SheetDescription className="text-sm">
            Type what you accept as correct, one answer per line. For a
            multiple-choice question, tick the correct option, or every correct
            one. Leave a question empty to grade it by hand. Saved straight into
            the answer key file in the class folder.
          </SheetDescription>
        </SheetHeader>
        {!open ? null : saved !== null ? (
          <>
            <div className="grid gap-3 px-4">
              <p>
                {saved === 1 ? '1 answer is' : `${saved} answers are`} saved in
                the answer key.
                {missing.length
                  ? ` ${missing.length} question${missing.length === 1 ? ' is' : 's are'} still without one.`
                  : ''}
              </p>
              <RegradeNote assignmentId={assignmentId} />
            </div>
            <SheetFooter>
              <Button onClick={close}>Done</Button>
            </SheetFooter>
          </>
        ) : fields.error ? (
          <div className="px-4">
            <ErrorAlert
              error={fields.error}
              title="Could not load the questions"
            >
              <Button variant="outline" onClick={() => void fields.refetch()}>
                Try again
              </Button>
            </ErrorAlert>
          </div>
        ) : !fresh ? (
          <p className="px-4 text-muted-foreground">Loading the questions…</p>
        ) : (
          <AnswersForm
            // A fresh read of the file is a fresh form, never a merge.
            key={fields.dataUpdatedAt}
            assignmentId={assignmentId}
            fields={missing}
            reloading={fields.isFetching}
            onReload={() => void fields.refetch()}
            onSaved={setSaved}
            onClose={close}
          />
        )}
      </SheetContent>
    </Sheet>
  );
}

function AnswersForm({
  assignmentId,
  fields,
  reloading,
  onReload,
  onSaved,
  onClose,
}: {
  assignmentId: string;
  /** Told how many answers a save wrote; the sheet shows it and outlives this form. */
  onSaved: (count: number) => void;
  fields: AnswerField[];
  reloading: boolean;
  onReload: () => void;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const [drafts, setDrafts] = useState<Record<string, AnswerDraft>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const filled = fields.filter(
    (field) => cleanAnswers(drafts[field.question_id] ?? []).length
  );
  // The file moved on underneath this form: only a reload can continue.
  const stale = error instanceof ApiError && error.status === 409;

  async function save() {
    setSaving(true);
    setError(null);
    try {
      await saveAnswers(assignmentId, {
        questions: filled.map((field) => ({
          question_id: field.question_id,
          version: field.version ?? null,
          answers: cleanAnswers(drafts[field.question_id] ?? []),
          expected_answers: field.answers,
        })),
      });
      // The save has succeeded; a refresh that fails is not a failed save.
      await client
        .invalidateQueries({ queryKey: ['assignments', assignmentId] })
        .catch(() => undefined);
      onSaved(filled.length);
    } catch (cause) {
      setError(cause);
    } finally {
      setSaving(false);
    }
  }

  if (!fields.length)
    return (
      <>
        <p className="px-4">
          Every question in this answer key has an answer. To change one, use
          Edit on that question.
        </p>
        <SheetFooter>
          <Button onClick={onClose}>Done</Button>
        </SheetFooter>
      </>
    );

  return (
    <>
      <div className="grid gap-5 px-4">
        {fields.map((field) => (
          <div key={field.question_id} className="grid gap-2">
            <p className="font-medium wrap-anywhere whitespace-pre-line">
              {field.label}. ({field.points} points) {field.prompt}
            </p>
            <AnswersInput
              field={field}
              value={drafts[field.question_id] ?? []}
              disabled={saving || reloading}
              onChange={(value) =>
                setDrafts((current) => ({
                  ...current,
                  [field.question_id]: value,
                }))
              }
            />
          </div>
        ))}
        {error !== null && (
          <ErrorAlert error={error} fallback="Could not save the answers.">
            {stale && (
              <Button variant="outline" disabled={reloading} onClick={onReload}>
                Load the current answer key
              </Button>
            )}
          </ErrorAlert>
        )}
      </div>
      <SheetFooter className="flex-row justify-end">
        <Button variant="outline" disabled={saving} onClick={onClose}>
          Not now
        </Button>
        <Button
          disabled={!filled.length || saving || reloading || stale}
          onClick={() => void save()}
        >
          {saving
            ? 'Saving…'
            : filled.length
              ? `Save ${filled.length} answer${filled.length === 1 ? '' : 's'}`
              : 'Save answers'}
        </Button>
      </SheetFooter>
    </>
  );
}
