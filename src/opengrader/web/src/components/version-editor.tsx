import { useId, useRef, useState, type Ref } from 'react';
import { Ellipsis, Plus } from 'lucide-react';
import type { DocumentEdit } from '@/api/queries';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from './ui/dropdown-menu';
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

type Version = NonNullable<DocumentEdit['versions']>[number];

const versionName = (version: Version) =>
  version.label ? `Version ${version.label}` : 'Unnamed version';

const cleanLabel = (label: string) => label.trim().replace(/^version\s+/i, '');

function nextLabel(versions: Version[]) {
  for (let number = 0; ; number += 1) {
    const label =
      number < 26 ? String.fromCharCode(65 + number) : String(number + 1);
    if (!versions.some((item) => item.label === label)) return label;
  }
}

export function VersionBar({
  ref,
  versions,
  questionCounts,
  selected,
  disabled,
  onChange,
  onSelect,
}: {
  ref?: Ref<HTMLDivElement>;
  versions: Version[];
  questionCounts: Record<string, number>;
  selected: string;
  disabled: boolean;
  onChange: (versions: Version[]) => void;
  onSelect: (key: string) => void;
}) {
  const [dialog, setDialog] = useState<'rename' | 'delete' | null>(null);
  const [name, setName] = useState('');
  const menuTriggerRef = useRef<HTMLButtonElement>(null);
  const nameId = useId();
  const current = versions.find((item) => item.key === selected) ?? versions[0];
  const currentCount = questionCounts[current.key] ?? 0;
  const label = cleanLabel(name);
  const nameProblem = !label
    ? 'Enter a name.'
    : versions.some((item) => item.key !== current.key && item.label === label)
      ? 'Another version already has this name.'
      : '';

  function addVersion() {
    const named = versions.map((item) => ({
      ...item,
      label: item.label.trim() || nextLabel(versions),
    }));
    const version = {
      key: crypto.randomUUID(),
      original_label: null,
      label: nextLabel(named),
    };
    onChange([...named, version]);
    onSelect(version.key);
  }

  const add = (
    <Button
      type="button"
      variant="ghost"
      disabled={disabled}
      onClick={addVersion}
    >
      <Plus aria-hidden="true" />
      Add version
    </Button>
  );

  return (
    <div
      ref={ref}
      className={
        versions.length === 1
          ? 'flex flex-wrap items-center justify-between gap-2 border-y py-1'
          : 'sticky top-0 z-10 flex flex-wrap items-center gap-2 border-b bg-background py-1'
      }
    >
      {versions.length === 1 ? (
        <>
          <p className="text-sm text-muted-foreground">
            Every student gets the same questions. Add a version to give some
            students different ones.
          </p>
          {add}
        </>
      ) : (
        <>
          <div
            role="group"
            aria-label="Version to edit"
            className="flex min-w-0 flex-wrap items-center gap-1"
          >
            {versions.map((version) => {
              const count = questionCounts[version.key] ?? 0;
              return (
                <Button
                  key={version.key}
                  type="button"
                  variant={version.key === current.key ? 'secondary' : 'ghost'}
                  aria-pressed={version.key === current.key}
                  disabled={disabled}
                  onClick={() => onSelect(version.key)}
                >
                  {versionName(version)}
                  <span className="text-xs text-muted-foreground tabular-nums">
                    {count}
                    <span className="sr-only">
                      {count === 1 ? ' question' : ' questions'}
                    </span>
                  </span>
                </Button>
              );
            })}
          </div>
          <div className="ms-auto flex items-center gap-1">
            {add}
            <DropdownMenu>
              <DropdownMenuTrigger
                ref={menuTriggerRef}
                disabled={disabled}
                render={
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    aria-label={`${versionName(current)} options`}
                    title={`${versionName(current)} options`}
                  />
                }
              >
                <Ellipsis aria-hidden="true" />
              </DropdownMenuTrigger>
              <DropdownMenuContent
                align="end"
                className="w-48"
                finalFocus={dialog ? false : undefined}
              >
                <DropdownMenuItem
                  onClick={() => {
                    setName(current.label);
                    setDialog('rename');
                  }}
                >
                  Rename…
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  variant="destructive"
                  onClick={() => setDialog('delete')}
                >
                  Delete version…
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </>
      )}
      <AlertDialog
        open={dialog !== null}
        onOpenChange={(open) => {
          if (!open) setDialog(null);
        }}
      >
        <AlertDialogContent finalFocus={menuTriggerRef}>
          {dialog === 'rename' ? (
            <form
              className="grid gap-4"
              onSubmit={(event) => {
                event.preventDefault();
                event.stopPropagation();
                if (nameProblem) return;
                onChange(
                  versions.map((item) =>
                    item.key === current.key ? { ...item, label } : item
                  )
                );
                setDialog(null);
              }}
            >
              <AlertDialogHeader>
                <AlertDialogTitle>
                  Rename {versionName(current)}
                </AlertDialogTitle>
              </AlertDialogHeader>
              <div className="space-y-2">
                <Label htmlFor={nameId}>Name</Label>
                <Input
                  id={nameId}
                  value={name}
                  autoFocus
                  aria-invalid={!!nameProblem}
                  aria-describedby={`${nameId}-hint`}
                  onChange={(event) => setName(event.target.value)}
                />
                <p
                  id={`${nameId}-hint`}
                  className={`text-xs ${nameProblem ? 'text-destructive' : 'text-muted-foreground'}`}
                >
                  {nameProblem || `Shown as "Version ${label}".`}
                </p>
              </div>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction type="submit" disabled={!!nameProblem}>
                  Rename
                </AlertDialogAction>
              </AlertDialogFooter>
            </form>
          ) : (
            <>
              <AlertDialogHeader>
                <AlertDialogTitle>
                  Delete {versionName(current)}?
                </AlertDialogTitle>
                <AlertDialogDescription className="text-pretty">
                  {currentCount
                    ? `Its ${currentCount} ${currentCount === 1 ? 'question' : 'questions'} will be removed from the answer key when you save.`
                    : 'It has no questions.'}{' '}
                  You can discard unsaved changes to undo this.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction
                  type="button"
                  variant="destructive"
                  onClick={() => {
                    const index = versions.indexOf(current);
                    const next = versions.filter(
                      (item) => item.key !== current.key
                    );
                    onChange(next);
                    onSelect(next[Math.max(0, index - 1)].key);
                    setDialog(null);
                  }}
                >
                  Delete version
                </AlertDialogAction>
              </AlertDialogFooter>
            </>
          )}
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
