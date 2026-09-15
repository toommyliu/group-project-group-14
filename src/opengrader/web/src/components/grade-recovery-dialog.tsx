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
import { Button } from './ui/button';

export function GradeRecoveryDialog({
  mode,
  busy,
  onCancel,
  onDiscard,
  onRetry,
}: {
  mode: 'leave' | 'reload' | null;
  busy: boolean;
  onCancel: () => void;
  onDiscard: () => void;
  onRetry: () => void;
}) {
  return (
    <AlertDialog
      open={mode !== null}
      onOpenChange={(open) => {
        if (!open && !busy) onCancel();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            {mode === 'reload'
              ? 'Reload the saved answer?'
              : 'Changes could not be saved'}
          </AlertDialogTitle>
          <AlertDialogDescription>
            {mode === 'reload'
              ? 'This replaces your unsaved grade and feedback with the saved answer.'
              : 'Stay here to keep editing, retry the save, or discard the unsaved changes and leave.'}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter className="flex-wrap">
          <AlertDialogCancel disabled={busy}>Stay here</AlertDialogCancel>
          <Button variant="outline" disabled={busy} onClick={onDiscard}>
            {mode === 'reload' ? 'Discard and reload' : 'Discard and leave'}
          </Button>
          {mode === 'leave' && (
            <AlertDialogAction disabled={busy} onClick={onRetry}>
              Retry save
            </AlertDialogAction>
          )}
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
