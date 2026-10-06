import { useEffect, useRef, useState, type ReactNode } from 'react';
import { LoaderCircle } from 'lucide-react';
import { cn } from 'cn';
import {
  studentLabel,
  type RosterImport,
  type RosterUpdate,
  type StudentDetails,
} from '@/api/roster';
import { ErrorAlert } from '@/components/error-alert';
import { SteadyLabel } from '@/components/steady-label';
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import { Table, TableBody, TableCell, TableRow } from '@/components/ui/table';
import { useRosterImport } from '@/hooks/use-roster-import';

const shownLimit = 50;
const number = new Intl.NumberFormat();

function count(value: number, singular: string) {
  return `${number.format(value)} ${value === 1 ? singular : `${singular}s`}`;
}

const columns = [
  { heading: 'First Name', example: 'Ana María', required: true },
  { heading: 'Last Name', example: 'de la Cruz', required: true },
  { heading: 'SID', example: '012345678', required: true },
  { heading: 'Email', example: 'ana@school.edu', required: false },
];

const rowNumber =
  'w-8 border-e bg-muted/50 text-center text-muted-foreground tabular-nums';

function CsvExample() {
  return (
    <div className="space-y-2">
      <Table aria-label="Example roster CSV file" className="border text-xs">
        <TableBody>
          <TableRow className="hover:bg-transparent">
            <TableCell className={rowNumber}>1</TableCell>
            {columns.map(({ heading, required }) => (
              <TableCell
                key={heading}
                className={cn(
                  'font-medium',
                  required && 'border-t-2 border-t-chart-2 bg-chart-2/20'
                )}
              >
                {heading}
                <span className="sr-only">
                  {required ? ' (required)' : ' (optional)'}
                </span>
              </TableCell>
            ))}
          </TableRow>
          <TableRow className="hover:bg-transparent">
            <TableCell className={rowNumber}>2</TableCell>
            {columns.map(({ heading, example, required }) => (
              <TableCell
                key={heading}
                className={cn(
                  'text-muted-foreground',
                  required && 'bg-chart-2/10'
                )}
              >
                {example}
              </TableCell>
            ))}
          </TableRow>
        </TableBody>
      </Table>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span aria-hidden="true" className="flex items-center gap-1.5">
          <span className="size-3 bg-chart-2" />
          Required
        </span>
        <span aria-hidden="true" className="flex items-center gap-1.5">
          <span className="size-3 border" />
          Optional
        </span>
        <span className="ms-auto">Other columns are ignored.</span>
      </div>
    </div>
  );
}

const scrollable =
  'max-h-64 overflow-y-auto overscroll-contain focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none';
const studentFields = ['First name', 'Last name', 'SID', 'Email'];

function NewStudents({ students }: { students: StudentDetails[] }) {
  const heading = count(students.length, 'new student');
  return (
    <div className="space-y-2">
      <h3 className="font-medium">{heading}</h3>
      <div
        role="table"
        aria-label={heading}
        className="grid grid-cols-[repeat(4,auto)] overflow-x-auto rounded-md border text-xs"
      >
        <div role="rowgroup" className="col-span-full grid grid-cols-subgrid">
          <div
            role="row"
            className="col-span-full grid grid-cols-subgrid border-b font-medium"
          >
            {studentFields.map((field) => (
              <div
                key={field}
                role="columnheader"
                className="p-2 whitespace-nowrap"
              >
                {field}
              </div>
            ))}
          </div>
        </div>
        <div
          role="rowgroup"
          tabIndex={0}
          className={cn('col-span-full grid grid-cols-subgrid', scrollable)}
        >
          {students.map((student) => (
            <div
              key={student.sid}
              role="row"
              className="col-span-full grid grid-cols-subgrid border-b last:border-b-0"
            >
              {[
                student.first_name,
                student.last_name,
                student.sid,
                student.email,
              ].map((value, index) => (
                <div
                  key={studentFields[index]}
                  role="cell"
                  className="p-2 whitespace-nowrap tabular-nums"
                >
                  {value}
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function Changes({ update }: { update: RosterUpdate }) {
  const { before, after } = update;
  const fields = [
    ['First name', before.first_name, after.first_name],
    ['Last name', before.last_name, after.last_name],
    ['Email', before.email ?? '', after.email ?? ''],
  ] as const;
  return (
    <li className="wrap-anywhere">
      <span className="font-medium">{studentLabel(after)}</span>
      <ul className="text-muted-foreground">
        {fields
          .filter(([, old, next]) => old !== next)
          .map(([label, old, next]) => (
            <li key={label}>
              {label}: {old || '(blank)'} <span aria-hidden="true">→</span>
              <span className="sr-only">changes to</span> {next || '(blank)'}
            </li>
          ))}
      </ul>
    </li>
  );
}

function More({ total, noun }: { total: number; noun: string }) {
  if (total <= shownLimit) return null;
  return (
    <p className="text-muted-foreground">
      And {count(total - shownLimit, noun)} more.
    </p>
  );
}

function importedMessage({ added, updated }: RosterImport) {
  const parts = [];
  if (added.length) parts.push(`Added ${count(added.length, 'student')}`);
  if (updated.length)
    parts.push(
      `${added.length ? 'updated' : 'Updated'} ${count(updated.length, 'student')}`
    );
  return `${parts.join(' and ')}.`;
}

export function RosterImportDialog({
  open,
  onOpenChange,
  onImported,
  finalFocus,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onImported: (message: string) => void;
  finalFocus: () => HTMLElement | null;
}) {
  const input = useRef<HTMLInputElement>(null);
  const title = useRef<HTMLHeadingElement>(null);
  const [dragging, setDragging] = useState(false);
  const rosterImport = useRosterImport((result) =>
    onImported(importedMessage(result))
  );
  const { state } = rosterImport;
  const importing = state.kind === 'importing';
  const canChoose = state.kind !== 'checking' && !importing;
  const arrived =
    state.kind === 'checking' ||
    state.kind === 'review' ||
    state.kind === 'unchecked'
      ? state
      : null;
  useEffect(() => {
    if (arrived) title.current?.focus();
  }, [arrived]);

  function choose(file: File) {
    void rosterImport.check(file);
  }

  const chooseButton = (label: string, primary = false) => (
    <Button
      type="button"
      variant={primary ? 'default' : 'outline'}
      onClick={() => input.current?.click()}
    >
      {label}
    </Button>
  );
  const cancel = (
    <AlertDialogCancel disabled={importing}>Cancel</AlertDialogCancel>
  );

  let heading: string;
  let body: ReactNode;
  let actions: ReactNode;
  if (state.kind === 'idle') {
    heading = 'Import students from a CSV file';
    body = <CsvExample />;
    actions = (
      <>
        {cancel}
        {chooseButton('Choose CSV file', true)}
      </>
    );
  } else if (state.kind === 'checking') {
    heading = `Checking ${state.file.name}`;
    body = (
      <p className="flex items-center gap-2 text-muted-foreground">
        <LoaderCircle
          className="size-4 motion-safe:animate-spin"
          aria-hidden="true"
        />
        Checking the file against the roster…
      </p>
    );
    actions = cancel;
  } else if (state.kind === 'unchecked') {
    heading = `${state.file.name} could not be checked`;
    body = <ErrorAlert error={state.error} />;
    actions = (
      <>
        {cancel}
        <Button variant="outline" onClick={() => choose(state.file)}>
          Try again
        </Button>
      </>
    );
  } else if (state.preview.problems.length) {
    const { problems } = state.preview;
    heading = `Fix ${state.file.name} before importing`;
    body = (
      <>
        <p>
          Nothing was imported. Fix{' '}
          {problems.length === 1
            ? 'this problem'
            : `these ${number.format(problems.length)} problems`}{' '}
          in the file, save it, and choose it again.
        </p>
        <ul className="space-y-1">
          {problems.slice(0, shownLimit).map((problem, index) => (
            <li key={index} className="wrap-anywhere">
              {problem.row ? (
                <span className="font-medium">Row {problem.row}: </span>
              ) : null}
              {problem.message}
            </li>
          ))}
        </ul>
        <More total={problems.length} noun="problem" />
      </>
    );
    actions = (
      <>
        {cancel}
        {chooseButton('Choose a fixed file', true)}
      </>
    );
  } else if (!state.preview.added.length && !state.preview.updated.length) {
    heading = `${state.file.name} matches the roster`;
    body = (
      <p>
        Everyone in this file is already on the roster with the same details.
      </p>
    );
    actions = <AlertDialogCancel>Done</AlertDialogCancel>;
  } else {
    const { file, preview } = state;
    heading = `Review ${file.name}`;
    body = (
      <>
        {state.kind === 'review' && state.rechecked && (
          <p>
            The roster changed after you chose this file, so it was checked
            again. Review the changes below.
          </p>
        )}
        {preview.added.length > 0 && <NewStudents students={preview.added} />}
        {preview.updated.length > 0 && (
          <div className="space-y-2">
            <h3 className="font-medium">
              {count(preview.updated.length, 'student')} with changed details
            </h3>
            <ul
              tabIndex={0}
              aria-label="Changed details"
              className={cn('space-y-2 rounded-md border p-2', scrollable)}
            >
              {preview.updated.map((update) => (
                <Changes key={update.after.sid} update={update} />
              ))}
            </ul>
          </div>
        )}
        <p className="text-muted-foreground">
          {preview.unchanged > 0 &&
            `${count(preview.unchanged, 'student')} already up to date. `}
          Students who aren't in this file stay on the roster.
        </p>
        {state.kind === 'failed' && (
          <ErrorAlert title="The roster wasn't imported." error={state.error} />
        )}
      </>
    );
    actions = (
      <>
        {cancel}
        <Button
          disabled={importing}
          onClick={() => void rosterImport.confirm(file, preview)}
        >
          <SteadyLabel
            busy={importing}
            idle="Import roster"
            working="Importing…"
          />
        </Button>
      </>
    );
  }

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next, details) => {
        if (importing) return details.cancel();
        onOpenChange(next);
      }}
      onOpenChangeComplete={(nowOpen) => {
        if (!nowOpen) rosterImport.cancel();
      }}
    >
      <AlertDialogContent
        finalFocus={finalFocus}
        className={cn(
          'max-h-[calc(100svh-2rem)] overflow-y-auto overscroll-contain data-[size=default]:sm:max-w-lg',
          dragging && 'ring-2 ring-ring'
        )}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(canChoose);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          const [file] = event.dataTransfer.files;
          if (file && canChoose) choose(file);
        }}
      >
        <AlertDialogHeader>
          <AlertDialogTitle
            ref={title}
            tabIndex={-1}
            className="wrap-anywhere outline-none"
          >
            {heading}
          </AlertDialogTitle>
        </AlertDialogHeader>
        <div className="min-w-0 space-y-3 text-sm">{body}</div>
        <input
          ref={input}
          type="file"
          accept=".csv,text/csv"
          className="hidden"
          aria-label="Roster CSV file"
          onChange={(event) => {
            const [file] = event.target.files ?? [];
            event.target.value = '';
            if (file) choose(file);
          }}
        />
        <AlertDialogFooter>{actions}</AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
