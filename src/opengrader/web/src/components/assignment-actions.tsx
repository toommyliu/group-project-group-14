import { errorMessage } from '@/lib/error-message';
import { useRef, useState } from 'react';
import { Link, useRouter } from '@tanstack/react-router';
import { ChevronDown } from 'lucide-react';
import {
  assignmentsQuery,
  deleteAssignment,
  type Assignment,
} from '@/api/queries';
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
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from './ui/dropdown-menu';

export function AssignmentActions({ assignment }: { assignment: Assignment }) {
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
      await deleteAssignment(assignment.id);
    } catch (cause) {
      setError(errorMessage(cause, 'Could not delete assignment. Try again.'));
      pending.current = false;
      setBusy(false);
      return;
    }
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
    <div className="mb-6 space-y-2">
      <DropdownMenu>
        <DropdownMenuTrigger
          ref={triggerRef}
          disabled={busy}
          render={<Button type="button" variant="outline" />}
        >
          {busy ? 'Deleting…' : 'Assignment actions'}
          <ChevronDown aria-hidden="true" />
        </DropdownMenuTrigger>
        <DropdownMenuContent
          className="w-48"
          finalFocus={confirmOpen ? false : undefined}
        >
          <DropdownMenuItem
            render={
              <Link
                to="/edit-assignment/$assignmentId"
                params={{ assignmentId: assignment.id }}
              />
            }
          >
            Edit assignment
          </DropdownMenuItem>
          <DropdownMenuItem
            variant="destructive"
            onClick={() => {
              setError('');
              setConfirmOpen(true);
            }}
          >
            Delete assignment
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
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
              <span className="inline-flex min-w-0 max-w-full align-bottom">
                <span className="shrink-0 whitespace-nowrap">
                  This permanently deletes "
                </span>

                <span
                  className="min-w-0 truncate"
                  title={assignment.title}
                >
                  {assignment.title}
                </span>

                <span className="shrink-0">"</span>
              </span>{" "}
              and its QA file. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
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
      {!confirmOpen && error && <p role="alert">{error}</p>}
    </div>
  );
}
