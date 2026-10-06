import { useRef } from 'react';
import { LoaderCircle } from 'lucide-react';
import type { AssignmentOutline } from '@/api/outline';
import type { BoxChanges } from '@/lib/outline-draft';
import { ErrorAlert } from './error-alert';
import { SteadyLabel } from './steady-label';
import { Alert, AlertDescription, AlertTitle } from './ui/alert';
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

type Submissions = NonNullable<AssignmentOutline['submissions']>;
type Recut = NonNullable<AssignmentOutline['recut']>;

const listFormat = new Intl.ListFormat('en', { type: 'conjunction' });

const count = (value: number, one: string, many: string) =>
  `${value} ${value === 1 ? one : many}`;

function consequences(
  submissions: Submissions,
  changes: BoxChanges,
  identityBoxes: boolean
) {
  const graded = changes.rows.reduce(
    (total, row) =>
      total + (row.leaf.id ? (submissions.graded[row.leaf.id] ?? 0) : 0),
    0
  );
  const empty = changes.rows
    .filter((row) => !row.leaf.boxes.length)
    .map((row) => row.label);
  const rematch =
    (changes.name || changes.studentId) && identityBoxes
      ? submissions.rematchable
      : 0;
  const notes = [
    graded > 0 &&
      `${count(graded, 'graded answer keeps its score', 'graded answers keep their scores')}.`,
    empty.length > 0 &&
      `${empty.length === 1 ? 'Question' : 'Questions'} ${listFormat.format(empty)} will have no answer to grade.`,
    rematch > 0 &&
      `${count(rematch, 'submission', 'submissions')} will be matched to the roster again.`,
  ].filter(Boolean);
  return notes.length
    ? notes.join(' ')
    : 'Graders will see answers cut from the new boxes.';
}

export function RecutConfirm({
  open,
  submissions,
  changes,
  identityBoxes,
  busy,
  error,
  onOpenChange,
  onConfirm,
}: {
  open: boolean;
  submissions: Submissions;
  changes: BoxChanges;
  identityBoxes: boolean;
  busy: boolean;
  error: string;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
}) {
  const cancel = useRef<HTMLButtonElement>(null);
  const crop = count(submissions.count, 'submission', 'submissions');
  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (!busy) onOpenChange(next);
      }}
    >
      <AlertDialogContent initialFocus={cancel}>
        <AlertDialogHeader>
          <AlertDialogTitle>Crop {crop} again?</AlertDialogTitle>
          <AlertDialogDescription>
            {consequences(submissions, changes, identityBoxes)}
          </AlertDialogDescription>
        </AlertDialogHeader>
        {error && <ErrorAlert error={error} />}
        <AlertDialogFooter>
          <AlertDialogCancel ref={cancel} disabled={busy}>
            Cancel
          </AlertDialogCancel>
          <AlertDialogAction
            type="button"
            variant="destructive"
            disabled={busy}
            onClick={onConfirm}
          >
            <SteadyLabel busy={busy} idle={`Crop ${crop}`} working="Saving…" />
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

export function RecutProgress({
  recut,
  retrying,
  retried,
  onRetry,
}: {
  recut: Recut;
  retrying: boolean;
  retried: boolean;
  onRetry: () => void;
}) {
  const total = recut.done + recut.remaining + recut.failures.length;
  if (recut.remaining > 0)
    return (
      <Alert role="status">
        <LoaderCircle aria-hidden="true" className="motion-safe:animate-spin" />
        <AlertTitle>Cropping answers from the new boxes</AlertTitle>
        <AlertDescription className="grid gap-2">
          {recut.done} of {count(total, 'submission', 'submissions')} cropped.
          You can change the outline again when this finishes.
          <div
            className="h-1.5 overflow-hidden bg-muted"
            role="progressbar"
            aria-label="Submissions cropped"
            aria-valuemin={0}
            aria-valuemax={total}
            aria-valuenow={recut.done}
          >
            <div
              className="h-full bg-chart-1 transition-[width] motion-reduce:transition-none"
              style={{ width: `${(recut.done / total) * 100}%` }}
            />
          </div>
        </AlertDescription>
      </Alert>
    );
  const failed = recut.failures.length;
  return (
    <ErrorAlert
      title={`${count(failed, 'submission', 'submissions')} could not be cropped again.`}
      error={`${retried ? 'Tried again, but graders' : 'Graders'} still see the old crops for ${failed === 1 ? 'it' : 'them'}.`}
    >
      <ul className="grid gap-1">
        {recut.failures.map((failure) => (
          <li key={failure.submission_id}>
            {failure.student_name || `Submission ${failure.submission_id}`}:{' '}
            {failure.error}
          </li>
        ))}
      </ul>
      <Button
        type="button"
        variant="outline"
        disabled={retrying}
        onClick={onRetry}
      >
        <SteadyLabel busy={retrying} idle="Retry" working="Retrying…" />
      </Button>
    </ErrorAlert>
  );
}
