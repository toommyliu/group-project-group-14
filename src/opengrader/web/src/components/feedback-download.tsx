import { useState } from 'react';
import { Link } from '@tanstack/react-router';
import { useMutation } from '@tanstack/react-query';
import { FileDown } from 'lucide-react';
import { downloadFeedback, type FeedbackJob } from '@/api/queries';
import { Button } from './ui/button';
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from './ui/alert-dialog';
import { ErrorAlert } from './error-alert';
import { SteadyLabel } from './steady-label';

export type FeedbackRow = {
  submissionId: number;
  name: string;
  complete: boolean;
  job: FeedbackJob | undefined;
};

function plural(count: number, one: string, many: string) {
  return `${count} ${count === 1 ? one : many}`;
}

export function FeedbackDownload({
  assignmentId,
  filename,
  label,
  rows,
}: {
  assignmentId: string;
  filename: string;
  label: string;
  rows: FeedbackRow[];
}) {
  const [open, setOpen] = useState(false);
  const graded = rows.filter((row) => row.complete);
  const ready = graded.filter((row) => row.job?.status === 'ready');
  const inProgress = rows.filter((row) => !row.complete);
  const updating = graded.filter(
    (row) => row.job?.status === 'pending' || row.job?.status === 'running'
  );
  const missing = graded.filter(
    (row) => row.job?.status !== 'ready' && !updating.includes(row)
  );

  const download = useMutation({
    mutationFn: () =>
      downloadFeedback(
        assignmentId,
        ready.map((row) => row.submissionId)
      ),
    onSuccess: (blob) => {
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = filename;
      link.click();
      URL.revokeObjectURL(link.href);
      setOpen(false);
    },
  });

  return (
    <>
      <Button
        variant="outline"
        onClick={() => {
          download.reset();
          setOpen(true);
        }}
      >
        <FileDown aria-hidden="true" />
        {label}
      </Button>
      <AlertDialog
        open={open}
        onOpenChange={(next) => {
          if (!download.isPending) setOpen(next);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{label}</AlertDialogTitle>
            <AlertDialogDescription>
              {ready.length
                ? `A zip file with ${plural(ready.length, 'PDF', 'PDFs')}, one for each fully graded submission. Each PDF has the scores and comments on the student's pages and a grade summary at the end.`
                : 'No feedback PDFs are ready to download yet.'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {(inProgress.length > 0 ||
            updating.length > 0 ||
            missing.length > 0) && (
            <div className="grid gap-3 text-sm">
              <p className="font-medium">Not included</p>
              <Skipped
                assignmentId={assignmentId}
                rows={inProgress}
                reason="Still being graded"
                onOpen={() => setOpen(false)}
              />
              <Skipped
                assignmentId={assignmentId}
                rows={updating}
                reason="Feedback PDF still updating. Try again in a moment."
                onOpen={() => setOpen(false)}
              />
              <Skipped
                assignmentId={assignmentId}
                rows={missing}
                reason="Feedback PDF could not be made. Open the student to try again."
                onOpen={() => setOpen(false)}
              />
            </div>
          )}
          {download.error && <ErrorAlert error={download.error.message} />}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={download.isPending}>
              Cancel
            </AlertDialogCancel>
            <Button
              disabled={ready.length === 0 || download.isPending}
              onClick={() => download.mutate()}
            >
              <SteadyLabel
                busy={download.isPending}
                idle={`Download ${plural(ready.length, 'PDF', 'PDFs')}`}
                working="Preparing zip…"
              />
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

const SHOWN = 8;

function Skipped({
  assignmentId,
  rows,
  reason,
  onOpen,
}: {
  assignmentId: string;
  rows: FeedbackRow[];
  reason: string;
  onOpen: () => void;
}) {
  if (rows.length === 0) return null;
  return (
    <div>
      <p className="text-muted-foreground">
        {reason} ({rows.length})
      </p>
      <p>
        {rows.slice(0, SHOWN).map((row, index) => (
          <span key={row.submissionId}>
            {index > 0 && ', '}
            <Link
              to="/assignments/$assignmentId/review/$submissionId"
              params={{ assignmentId, submissionId: String(row.submissionId) }}
              onClick={onOpen}
              className="underline underline-offset-4"
            >
              {row.name}
            </Link>
          </span>
        ))}
        {rows.length > SHOWN && `, and ${rows.length - SHOWN} more`}
      </p>
    </div>
  );
}
