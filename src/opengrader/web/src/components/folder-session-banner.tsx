import { useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { TriangleAlert, X } from 'lucide-react';
import {
  convertFolderSession,
  workspaceQuery,
  type FolderSessionInfo,
} from '@/api/workspace';
import { useFolderSessionSync } from '@/hooks/use-folder-session-sync';
import { errorMessage } from '@/lib/error-message';
import { ErrorAlert } from '@/components/error-alert';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';

export function FolderSessionBanner({
  session,
}: {
  session: FolderSessionInfo;
}) {
  const queryClient = useQueryClient();
  const { problems, dismissProblems, notice, dismissNotice } =
    useFolderSessionSync();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [converting, setConverting] = useState(false);
  const [error, setError] = useState('');
  const cancelRef = useRef<HTMLButtonElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  async function convert() {
    setConverting(true);
    setError('');
    try {
      await convertFolderSession();
      await queryClient.invalidateQueries({
        queryKey: workspaceQuery.queryKey,
      });
      setConfirmOpen(false);
    } catch (cause) {
      setError(errorMessage(cause, 'Could not create a workspace. Try again.'));
    } finally {
      setConverting(false);
    }
  }

  return (
    <div className="mb-4 shrink-0 space-y-2">
      <Alert>
        <AlertDescription className="flex flex-wrap items-center justify-between gap-3 text-sm text-foreground">
          <span className="min-w-0">
            Folder session — grades are saved into the files in{' '}
            <code className="break-all">{session.folder}</code>.
          </span>
          <Button
            ref={triggerRef}
            type="button"
            variant="outline"
            size="sm"
            className="shrink-0"
            onClick={() => {
              setError('');
              setConfirmOpen(true);
            }}
          >
            Create a workspace
          </Button>
        </AlertDescription>
      </Alert>
      {notice && (
        <Alert>
          <AlertDescription className="flex items-center justify-between gap-3 text-sm text-foreground">
            <span role="status">{notice}</span>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label="Dismiss"
              onClick={dismissNotice}
            >
              <X aria-hidden="true" />
            </Button>
          </AlertDescription>
        </Alert>
      )}
      {problems.length > 0 && (
        <Alert variant="destructive">
          <TriangleAlert aria-hidden="true" />
          <AlertTitle className="flex items-center justify-between gap-3">
            Some files could not be synced
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label="Dismiss"
              onClick={dismissProblems}
            >
              <X aria-hidden="true" />
            </Button>
          </AlertTitle>
          <AlertDescription>
            <ul className="list-disc space-y-1 ps-4">
              {problems.map((problem, index) => (
                <li key={index}>{problem}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      )}
      <AlertDialog
        open={confirmOpen}
        onOpenChange={(open) => {
          if (!converting) setConfirmOpen(open);
        }}
      >
        <AlertDialogContent initialFocus={cancelRef} finalFocus={triggerRef}>
          <AlertDialogHeader>
            <AlertDialogTitle>Create a workspace?</AlertDialogTitle>
            <AlertDialogDescription>
              A workspace lets several graders work on this assignment at once.
              Grades will then be kept in the workspace and no longer written
              into the .submission.md files automatically — write them back
              later with <code>opengrader tui</code> → W. This can't be undone
              from the browser.
            </AlertDialogDescription>
          </AlertDialogHeader>
          {error && <ErrorAlert error={error} />}
          <AlertDialogFooter>
            <AlertDialogCancel ref={cancelRef} disabled={converting}>
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              type="button"
              disabled={converting}
              onClick={() => void convert()}
            >
              {converting ? 'Creating…' : 'Create a workspace'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
