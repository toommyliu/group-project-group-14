import { useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { ChevronDown } from 'lucide-react';
import {
  deleteScan,
  removeScan,
  restoreScan,
  scansQuery,
  type ScanBatch,
} from '@/api/scans';
import { errorMessage } from '@/lib/error-message';
import { Button } from './ui/button';
import { Alert, AlertDescription } from './ui/alert';
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
} from './ui/dropdown-menu';
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogCancel,
  AlertDialogAction,
} from './ui/alert-dialog';

export function ScanActions({
  assignmentId,
  batch,
  disabled,
  onPendingChange,
}: {
  assignmentId: string;
  batch: ScanBatch;
  disabled: boolean;
  onPendingChange: (pending: boolean) => void;
}) {
  const client = useQueryClient();
  const pending = useRef(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [error, setError] = useState('');
  const [working, setWorking] = useState(false);
  async function run(action: 'delete' | 'remove' | 'restore') {
    if (disabled || pending.current) return;
    pending.current = true;
    setWorking(true);
    onPendingChange(true);
    setError('');
    try {
      const updated =
        action === 'delete'
          ? await deleteScan(assignmentId, batch)
          : action === 'remove'
            ? await removeScan(assignmentId, batch)
            : await restoreScan(assignmentId, batch);
      const queryKey = scansQuery(assignmentId).queryKey;
      await client.cancelQueries({ queryKey });
      client.setQueryData(
        queryKey,
        (previous) =>
          previous && {
            ...previous,
            batches: updated
              ? previous.batches.map((item) =>
                  item.id === batch.id ? updated : item
                )
              : previous.batches.filter((item) => item.id !== batch.id),
          }
      );
      if (action === 'delete') {
        await client.invalidateQueries({
          queryKey: ['assignments', assignmentId],
        });
      }
      setConfirmOpen(false);
    } catch (cause) {
      setError(
        errorMessage(
          cause,
          'Could not update this scan. Reload scans and try again.'
        )
      );
    } finally {
      pending.current = false;
      setWorking(false);
      onPendingChange(false);
    }
  }

  return (
    <div>
      <DropdownMenu>
        <DropdownMenuTrigger
          ref={trigger}
          disabled={disabled}
          render={<Button variant="outline" />}
        >
          Scan actions <ChevronDown aria-hidden="true" />
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="end"
          className="w-64 max-w-[calc(100vw-2rem)]"
          finalFocus={confirmOpen ? false : undefined}
        >
          <DropdownMenuItem
            onClick={() =>
              void run(batch.status === 'removed' ? 'restore' : 'remove')
            }
          >
            {batch.status === 'removed' ? 'Restore scan' : 'Remove scan'}
          </DropdownMenuItem>
          <DropdownMenuItem
            variant="destructive"
            onClick={() => {
              setError('');
              setConfirmOpen(true);
            }}
          >
            Delete scan
          </DropdownMenuItem>
          <p className="border-t px-2 py-2 text-xs text-muted-foreground">
            Remove keeps the scan and existing submissions so it can be
            restored. Delete permanently erases the scan and all submissions and
            grades created from it.
          </p>
        </DropdownMenuContent>
      </DropdownMenu>
      {error && !confirmOpen && (
        <Alert variant="destructive" className="mt-2">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <AlertDialog
        open={confirmOpen}
        onOpenChange={(open) => {
          if (!pending.current) setConfirmOpen(open);
        }}
      >
        <AlertDialogContent initialFocus={cancel} finalFocus={trigger}>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete scan?</AlertDialogTitle>
            <AlertDialogDescription className="break-words">
              This permanently deletes "{batch.filename}", its scan review, and
              all submissions and grades created from this scan. This cannot be
              undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel ref={cancel} disabled={working}>
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={disabled}
              onClick={() => void run('delete')}
            >
              {working ? 'Deleting…' : 'Delete scan'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
