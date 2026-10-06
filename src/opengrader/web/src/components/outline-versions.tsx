import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Plus, Trash2 } from 'lucide-react';
import {
  addVersion,
  outlineQuery,
  removeVersion,
  type AssignmentOutline,
} from '@/api/outline';
import { errorMessage } from '@/lib/error-message';
import { ErrorAlert } from './error-alert';
import { Button } from './ui/button';
import { Checkbox } from './ui/checkbox';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Popover, PopoverContent, PopoverTrigger } from './ui/popover';
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
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './ui/select';

const letters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('');

export function OutlineVersions({
  assignmentId,
  outline,
  dirty,
  onVersion,
  onBusyChange,
}: {
  assignmentId: string;
  outline: AssignmentOutline;
  dirty: boolean;
  onVersion: (version: string) => Promise<void>;
  onBusyChange: (busy: boolean) => void;
}) {
  const client = useQueryClient();
  const versioned = outline.versions[0] !== '';
  const current = outline.version || 'A';
  const taken = versioned ? outline.versions : ['A'];
  const [adding, setAdding] = useState(false);
  const [label, setLabel] = useState('');
  const [copy, setCopy] = useState(true);
  const [removing, setRemoving] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const locked = outline.lock !== null;

  async function run(action: () => Promise<AssignmentOutline>) {
    setBusy(true);
    onBusyChange(true);
    setError('');
    try {
      const next = await action();
      client.setQueryData(
        outlineQuery(assignmentId, next.version).queryKey,
        next
      );
      void client.invalidateQueries({
        queryKey: ['assignments', assignmentId],
      });
      setAdding(false);
      setRemoving(false);
      await onVersion(next.version);
    } catch (cause) {
      setError(
        errorMessage(cause, 'Could not change the versions. Try again.')
      );
    } finally {
      setBusy(false);
      onBusyChange(false);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      {versioned && (
        <Select
          value={outline.version}
          onValueChange={(value) => {
            if (value !== null && value !== outline.version)
              void onVersion(value);
          }}
        >
          <SelectTrigger
            aria-label="Version"
            className="min-h-11 min-w-36 text-sm"
          >
            <SelectValue>{`Version ${outline.version}`}</SelectValue>
          </SelectTrigger>
          <SelectContent align="end">
            {outline.versions.map((version) => (
              <SelectItem key={version} value={version}>
                Version {version}
                {!outline.versions_with_regions.includes(version) &&
                  ' · no boxes yet'}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
      {!locked && (
        <Popover
          open={adding}
          onOpenChange={(open) => {
            setAdding(open);
            setError('');
            if (open) {
              setLabel(letters.find((letter) => !taken.includes(letter)) ?? '');
              setCopy(true);
            }
          }}
        >
          <PopoverTrigger render={<Button type="button" variant="outline" />}>
            <Plus aria-hidden="true" />
            Add version
          </PopoverTrigger>
          <PopoverContent>
            <form
              className="grid gap-4"
              onSubmit={(event) => {
                event.preventDefault();
                void run(() =>
                  addVersion(assignmentId, label.trim(), copy ? current : null)
                );
              }}
            >
              <p className="text-sm text-muted-foreground">
                {versioned
                  ? 'Each version has its own PDF, question order, and boxes.'
                  : 'The current questions become version A. Each version has its own PDF, question order, and boxes.'}
              </p>
              <div className="grid gap-2">
                <Label htmlFor="version-name" className="text-sm">
                  Version name
                </Label>
                <Input
                  id="version-name"
                  required
                  maxLength={20}
                  value={label}
                  className="h-9 text-sm md:text-sm"
                  onChange={(event) => setLabel(event.target.value)}
                />
              </div>
              <Label className="flex items-center gap-2 text-sm leading-normal">
                <Checkbox checked={copy} onCheckedChange={setCopy} />
                Start with a copy of version {current}'s questions
              </Label>
              {dirty && (
                <p className="text-sm text-destructive">
                  Save your outline changes before adding a version.
                </p>
              )}
              {error && <ErrorAlert error={error} />}
              <Button
                type="submit"
                className="justify-self-start"
                disabled={busy || dirty || !label.trim()}
              >
                {busy ? 'Adding…' : 'Add version'}
              </Button>
            </form>
          </PopoverContent>
        </Popover>
      )}
      {!locked && versioned && outline.versions.length > 1 && (
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="size-11"
          aria-label={`Remove version ${outline.version}`}
          title={`Remove version ${outline.version}`}
          onClick={() => {
            setError('');
            setRemoving(true);
          }}
        >
          <Trash2 aria-hidden="true" />
        </Button>
      )}
      <AlertDialog
        open={removing}
        onOpenChange={(open) => {
          if (!busy) setRemoving(open);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Remove version {outline.version}?
            </AlertDialogTitle>
            <AlertDialogDescription>
              This deletes version {outline.version}'s questions, answers,
              rubric, template, and boxes. The other versions stay as they are.
              {dirty && ' Save or discard your outline changes first.'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {error && <ErrorAlert error={error} />}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              type="button"
              variant="destructive"
              disabled={busy || dirty}
              onClick={() =>
                void run(() => removeVersion(assignmentId, outline.version))
              }
            >
              {busy ? 'Removing…' : `Remove version ${outline.version}`}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
