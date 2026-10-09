import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { TriangleAlert } from 'lucide-react';
import {
  assignmentQAQuery,
  saveRubric,
  type QAPreview,
  type RubricItem,
} from '@/api/queries';
import { ApiError } from '@/api/client';
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
 * Edit one question's rubric while grading, in a folder session.
 *
 * It opens on the rubric as the answer key file has it now and saves
 * straight back into that file, so there is no draft to lose and nothing to
 * "send" afterwards. The server refuses the save if the rubric changed
 * underneath it or if it drops an item that saved grades still use.
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
  const found = target && qa.data ? findQuestion(qa.data, target) : null;
  return (
    <Sheet
      open={target !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <SheetContent className="gap-0 overflow-y-auto text-sm data-[side=right]:w-full data-[side=right]:sm:max-w-2xl">
        <SheetHeader>
          <SheetTitle className="text-base">
            Edit rubric{target ? ` · Question ${target.questionId}` : ''}
          </SheetTitle>
          <SheetDescription className="text-sm">
            Saved straight into the answer key file in the class folder. Grades
            that already use an item you rename or re-score follow it. An item
            that saved grades use cannot be removed.
          </SheetDescription>
        </SheetHeader>
        {target === null ? null : qa.isFetching && !found ? (
          <p className="px-4 text-muted-foreground">Loading the rubric…</p>
        ) : qa.error ? (
          <div className="px-4">
            <ErrorAlert error={qa.error} title="Could not load the rubric">
              <Button variant="outline" onClick={() => void qa.refetch()}>
                Try again
              </Button>
            </ErrorAlert>
          </div>
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
            key={`${target.questionId}/${target.version ?? ''}/${qa.dataUpdatedAt}`}
            assignmentId={assignmentId}
            target={target}
            opened={found.rubric}
            points={found.points}
            reloading={qa.isFetching}
            onReload={() => void qa.refetch()}
            onClose={onClose}
          />
        )}
      </SheetContent>
    </Sheet>
  );
}

function RubricForm({
  assignmentId,
  target,
  opened,
  points,
  reloading,
  onReload,
  onClose,
}: {
  assignmentId: string;
  target: RubricTarget;
  opened: RubricItem[];
  points: number;
  reloading: boolean;
  onReload: () => void;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const [rubric, setRubric] = useState(opened);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [problems, setProblems] = useState<string[] | null>(null);
  const changed = JSON.stringify(rubric) !== JSON.stringify(opened);
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
      });
      // Every answer to this question now carries the new rubric. The pages
      // that show it read it through these queries, so this is all the
      // refresh there is — and the save has already succeeded, so a refresh
      // that fails (the server's own change notice may be refetching the
      // same data at this moment) is not reported as a failed save.
      await client
        .invalidateQueries({ queryKey: ['assignments', assignmentId] })
        .catch(() => undefined);
      if (result.problems.length) setProblems(result.problems);
      else onClose();
    } catch (cause) {
      setError(cause);
    } finally {
      setSaving(false);
    }
  }

  if (problems)
    return (
      <>
        <div className="grid gap-3 px-4">
          <p className="flex items-start gap-2">
            <TriangleAlert
              className="mt-0.5 size-4 shrink-0"
              aria-hidden="true"
            />
            <span>
              The rubric is saved in the answer key. Some grades that use it
              could not be written into their files yet; they are saved here and
              are written the next time this page syncs with the folder.
            </span>
          </p>
          <ul className="list-disc space-y-1 ps-9 text-muted-foreground">
            {problems.map((problem, index) => (
              <li key={index} className="wrap-anywhere">
                {problem}
              </li>
            ))}
          </ul>
        </div>
        <SheetFooter>
          <Button onClick={onClose}>Done</Button>
        </SheetFooter>
      </>
    );

  return (
    <>
      <div className="grid gap-4 px-4">
        <RubricEditor
          rubric={rubric}
          points={points}
          disabled={saving || reloading}
          onChange={setRubric}
        />
        {error !== null && (
          <ErrorAlert error={error} fallback="Could not save the rubric.">
            {stale && (
              <Button variant="outline" disabled={reloading} onClick={onReload}>
                Load the current rubric
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
          {saving ? 'Saving…' : 'Save rubric'}
        </Button>
      </SheetFooter>
    </>
  );
}
