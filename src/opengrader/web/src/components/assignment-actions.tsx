import { errorMessage } from '@/lib/error-message';
import { useRef, useState } from 'react';
import { useRouter } from '@tanstack/react-router';
import {
  assignmentsQuery,
  deleteAssignment,
  type Assignment,
} from '@/api/queries';
import { ErrorAlert } from '@/components/error-alert';
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

export function AssignmentActions({
  assignment,
  onDeleted,
}: {
  assignment: Assignment;
  onDeleted: () => void;
}) {
  const router = useRouter();
  const pending = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [confirmOpen, setConfirmOpen] = useState(false);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  async function remove() {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError('');
    try {
      await deleteAssignment(assignment.id, true);
    } catch (cause) {
      setError(errorMessage(cause, 'Could not delete assignment. Try again.'));
      pending.current = false;
      setBusy(false);
      return;
    }
    onDeleted();
    setConfirmOpen(false);
    try {
      const client = router.options.context.queryClient;
      await client.cancelQueries({ queryKey: assignmentsQuery.queryKey });
      client.setQueryData(assignmentsQuery.queryKey, (previous) =>
        (previous ?? []).filter((item) => item.id !== assignment.id)
      );
      client.removeQueries({ queryKey: ['assignments', assignment.id] });
      await router.navigate({ to: '/' });
      await router.invalidate();
    } catch {
      setError('Assignment deleted. Return to the assignment list.');
    }
  }

  return (
    <div className="max-w-full">
      <div className="flex flex-wrap items-center gap-2">
        <Button
          ref={triggerRef}
          type="button"
          variant="destructive"
          disabled={busy}
          onClick={() => {
            setError('');
            setConfirmOpen(true);
          }}
        >
          {busy ? 'Deleting…' : 'Delete assignment'}
        </Button>
      </div>
      <AlertDialog
        open={confirmOpen}
        onOpenChange={(open) => {
          if (!pending.current) setConfirmOpen(open);
        }}
      >
        <AlertDialogContent initialFocus={cancelRef} finalFocus={triggerRef}>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete assignment?</AlertDialogTitle>
            <AlertDialogDescription className="w-full min-w-0 text-pretty">
              <span className="inline-flex max-w-full min-w-0 align-bottom">
                <span className="shrink-0 whitespace-nowrap">
                  This permanently deletes "
                </span>

                <span className="min-w-0 truncate" title={assignment.title}>
                  {assignment.title}
                </span>

                <span className="shrink-0">"</span>
              </span>{' '}
              and all of its scans, submissions, grades, and QA files. Any
              running terminal sessions will stop immediately. Any running
              processing will stop after its current step. This cannot be
              undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          {error && (
            <ErrorAlert
              className="items-center [&>svg]:row-span-1"
              error={error}
            />
          )}
          <AlertDialogFooter>
            <AlertDialogCancel ref={cancelRef} disabled={busy}>
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              type="button"
              variant="destructive"
              disabled={busy}
              onClick={() => void remove()}
            >
              {busy ? 'Deleting…' : 'Delete assignment'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
