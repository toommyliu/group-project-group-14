import {
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type FormEvent,
} from 'react';
import { useWindowVirtualizer } from '@tanstack/react-virtual';
import {
  columnFilteringFeature,
  createColumnHelper,
  createFilteredRowModel,
  createSortedRowModel,
  filterFn_equalsString,
  filterFn_includesString,
  globalFilteringFeature,
  rowSortingFeature,
  sortFn_text,
  tableFeatures,
  useTable,
} from '@tanstack/react-table';
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  Check,
  Copy,
  Search,
  X,
} from 'lucide-react';
import { cn } from 'cn';
import type { People } from '@/api/people';
import { ErrorAlert } from '@/components/error-alert';
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
import { PageHeading } from '@/components/page-heading';
import { SteadyLabel } from '@/components/steady-label';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { errorMessage } from '@/lib/error-message';
import type { Role } from '@/api/auth';

type Person = { email: string; role: Role };

const roles: Record<Role, string> = {
  instructor: 'Instructor',
  grader: 'Grader',
};

const roleFilters = [
  { value: 'all', label: 'All roles' },
  { value: 'instructor', label: 'Instructors' },
  { value: 'grader', label: 'Graders' },
] as const;

const features = tableFeatures({
  rowSortingFeature,
  sortedRowModel: createSortedRowModel(),
  columnFilteringFeature,
  globalFilteringFeature,
  filteredRowModel: createFilteredRowModel(),
  sortFns: { text: sortFn_text },
  filterFns: {
    includesString: filterFn_includesString,
    equalsString: filterFn_equalsString,
  },
});
const column = createColumnHelper<typeof features, Person>();
const columns = column.columns([
  column.accessor('email', { header: 'Email', sortFn: 'text' }),
  column.accessor('role', {
    header: 'Role',
    sortFn: 'text',
    filterFn: 'equalsString',
    enableGlobalFilter: false,
  }),
]);

const copyLabels = {
  idle: 'Copy email',
  copied: 'Copied',
  failed: "Couldn't copy",
} as const;

function CopyEmail({ email }: { email: string }) {
  const [result, setResult] = useState<'copied' | 'failed' | null>(null);
  const [open, setOpen] = useState(false);
  const dismiss = useRef<ReturnType<typeof setTimeout>>(undefined);
  const label = copyLabels[result ?? 'idle'];

  async function copy() {
    let next: 'copied' | 'failed' = 'copied';
    try {
      await navigator.clipboard.writeText(email);
    } catch {
      next = 'failed';
    }
    clearTimeout(dismiss.current);
    setResult(next);
    setOpen(true);
    dismiss.current = setTimeout(() => setOpen(false), 1500);
  }

  return (
    <>
      <Tooltip
        open={open}
        onOpenChange={(next, details) => {
          if (!next && details.reason === 'trigger-press') return;
          setOpen(next);
        }}
        onOpenChangeComplete={(nowOpen) => {
          if (!nowOpen) setResult(null);
        }}
      >
        <TooltipTrigger
          render={
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label={`Copy ${email}`}
              className="-ms-1 shrink-0 group-focus-within:opacity-100 group-hover:opacity-100 [@media(hover:hover)]:opacity-0"
              onClick={() => void copy()}
            />
          }
        >
          {result === 'copied' ? (
            <Check aria-hidden="true" />
          ) : result === 'failed' ? (
            <X aria-hidden="true" />
          ) : (
            <Copy aria-hidden="true" />
          )}
        </TooltipTrigger>
        <TooltipContent>{label}</TooltipContent>
      </Tooltip>
      <span role="status" className="sr-only">
        {result ? `${label} ${email}` : ''}
      </span>
    </>
  );
}

const emailTailLength = 16;

function breakBeforeDomain(text: string) {
  const at = text.indexOf('@');
  if (at < 0) return text;
  return (
    <>
      {text.slice(0, at)}
      <wbr className="sm:hidden" />
      {text.slice(at)}
    </>
  );
}

function Email({ email }: { email: string }) {
  return (
    <span
      title={email}
      className="min-w-0 wrap-anywhere whitespace-normal sm:flex sm:whitespace-nowrap"
    >
      <span className="sm:truncate">
        {breakBeforeDomain(email.slice(0, -emailTailLength))}
      </span>
      <span className="sm:shrink-0">
        {breakBeforeDomain(email.slice(-emailTailLength))}
      </span>
    </span>
  );
}

const number = new Intl.NumberFormat();

function count(value: number, singular: string) {
  return `${number.format(value)} ${value === 1 ? singular : `${singular}s`}`;
}

export function PeopleManager({
  people,
  me,
  onAdd,
  onRemove,
}: {
  people: People;
  me?: string;
  onAdd: (email: string) => Promise<void>;
  onRemove: (email: string) => Promise<void>;
}) {
  'use no memo';

  const id = useId();
  const [email, setEmail] = useState('');
  const [addError, setAddError] = useState('');
  const [addNotice, setAddNotice] = useState('');
  const [confirming, setConfirming] = useState<string | null>(null);
  const [removeError, setRemoveError] = useState('');
  const [announcement, setAnnouncement] = useState('');
  const [pending, setPending] = useState<string | null>(null);
  const busy = useRef(false);
  const input = useRef<HTMLInputElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const removed = useRef(false);
  const opener = useRef<HTMLElement | null>(null);
  const body = useRef<HTMLTableSectionElement>(null);
  const [scrollMargin, setScrollMargin] = useState(0);

  const data: Person[] = [
    ...people.instructors.map((address) => ({
      email: address,
      role: 'instructor' as const,
    })),
    ...people.graders.map((address) => ({
      email: address,
      role: 'grader' as const,
    })),
  ];
  const table = useTable({
    features,
    columns,
    data,
    getRowId: (person) => person.email,
    globalFilterFn: 'includesString',
    enableSortingRemoval: false,
  });
  const emailFilter = String(table.state.globalFilter ?? '');
  const roleColumn = table.getColumn('role');
  const roleFilter = String(roleColumn?.getFilterValue() ?? 'all');
  const filtered = Boolean(emailFilter.trim()) || roleFilter !== 'all';
  const rows = table.getRowModel().rows;
  const summary = `${count(people.instructors.length, 'instructor')} · ${count(people.graders.length, 'grader')}`;

  useLayoutEffect(() => {
    function measure() {
      const top = body.current!.getBoundingClientRect().top + window.scrollY;
      setScrollMargin((current) =>
        Math.abs(current - top) < 0.5 ? current : top
      );
    }
    const observer = new ResizeObserver(measure);
    observer.observe(document.body);
    return () => observer.disconnect();
  }, []);

  const virtualizer = useWindowVirtualizer({
    count: rows.length,
    estimateSize: () => 44,
    getItemKey: (index) => rows[index].id,
    overscan: 10,
    scrollMargin,
  });
  const visible = virtualizer.getVirtualItems();
  const before = visible.length ? visible[0].start - scrollMargin : 0;
  const after = visible.length
    ? virtualizer.getTotalSize() - (visible.at(-1)!.end - scrollMargin)
    : 0;

  function start(action: string) {
    busy.current = true;
    setPending(action);
    setAddError('');
    setAddNotice('');
    setRemoveError('');
    setAnnouncement('');
  }

  function finish() {
    busy.current = false;
    setPending(null);
  }

  async function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy.current) return;
    const address = email.trim().toLowerCase();
    if (!address) {
      setAddNotice('');
      setAddError(`Enter one @${people.domain} email address.`);
      input.current?.focus();
      return;
    }
    start('add');
    try {
      await onAdd(address);
      setEmail('');
      setAddNotice(`Added ${address}.`);
    } catch (cause) {
      setAddError(errorMessage(cause, 'Could not add this grader. Try again.'));
    } finally {
      finish();
      input.current?.focus();
    }
  }

  async function remove(grader: string) {
    if (busy.current) return;
    start('remove');
    try {
      await onRemove(grader);
      removed.current = true;
      setConfirming(null);
      setAnnouncement(`Removed ${grader}.`);
    } catch (cause) {
      setRemoveError(
        errorMessage(cause, 'Could not remove this grader. Try again.')
      );
    } finally {
      finish();
    }
  }

  return (
    <>
      <PageHeading
        title="People"
        description="Everyone listed here can sign in and grade every assignment."
      />

      <section aria-labelledby={`${id}-add`} className="space-y-4 pb-8">
        <div className="space-y-1">
          <h2 id={`${id}-add`} className="text-base font-semibold">
            Add a grader
          </h2>
          <p className="max-w-prose text-sm wrap-anywhere text-muted-foreground">
            Graders sign in with their @{people.domain} Google account and can
            do everything except manage this list. Removing a grader signs them
            out right away.
          </p>
        </div>

        <form
          noValidate
          onSubmit={(event) => void add(event)}
          className="grid max-w-xl gap-2"
        >
          <Label htmlFor={`${id}-email`} className="text-sm">
            Grader email
          </Label>
          <div className="flex gap-2">
            <Input
              ref={input}
              id={`${id}-email`}
              type="email"
              inputMode="email"
              autoComplete="off"
              autoCapitalize="none"
              spellCheck={false}
              placeholder={`name@${people.domain}`}
              value={email}
              onChange={(event) => {
                setEmail(event.target.value);
                setAddNotice('');
              }}
              aria-invalid={addError ? true : undefined}
              aria-describedby={addError ? `${id}-email-feedback` : undefined}
              className="h-11 flex-1 text-sm text-ellipsis md:text-sm"
            />
            <Button type="submit" disabled={pending === 'add'}>
              <SteadyLabel
                busy={pending === 'add'}
                idle="Add grader"
                working="Adding…"
              />
            </Button>
          </div>
          <p
            id={`${id}-email-feedback`}
            role="status"
            className={cn(
              'min-h-5 text-sm wrap-anywhere',
              addError ? 'text-destructive' : 'text-muted-foreground'
            )}
          >
            {addError || addNotice}
          </p>
        </form>
      </section>

      <section aria-labelledby={`${id}-people`} className="space-y-3">
        <h2
          id={`${id}-people`}
          ref={heading}
          tabIndex={-1}
          className="text-base font-semibold outline-none"
        >
          Who has access
        </h2>

        <div className="flex flex-wrap items-center gap-2">
          <div className="relative w-full sm:w-64">
            <Search
              aria-hidden="true"
              className="pointer-events-none absolute start-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
            />
            <Input
              type="search"
              aria-label="Filter by email"
              placeholder="Filter by email"
              autoComplete="off"
              spellCheck={false}
              value={emailFilter}
              onChange={(event) => table.setGlobalFilter(event.target.value)}
              className="ps-8 text-ellipsis"
            />
          </div>
          <Select
            items={roleFilters}
            value={roleFilter}
            onValueChange={(value) =>
              roleColumn?.setFilterValue(value === 'all' ? undefined : value)
            }
          >
            <SelectTrigger aria-label="Filter by role" className="w-36">
              <SelectValue />
            </SelectTrigger>
            <SelectContent align="start" alignItemWithTrigger={false}>
              {roleFilters.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p
            role="status"
            className="ms-auto grid justify-items-end text-sm text-muted-foreground tabular-nums"
          >
            <span className="col-start-1 row-start-1">
              {filtered
                ? `Showing ${number.format(rows.length)} of ${number.format(data.length)}`
                : summary}
            </span>
            <span className="invisible col-start-1 row-start-1">{summary}</span>
          </p>
        </div>

        <p role="status" className="sr-only">
          {announcement}
        </p>

        <Table
          className="text-sm"
          aria-labelledby={`${id}-people`}
          aria-rowcount={rows.length ? rows.length + 1 : undefined}
        >
          <TableHeader>
            {table.getHeaderGroups().map((group) => (
              <TableRow
                key={group.id}
                aria-rowindex={1}
                className="hover:bg-transparent"
              >
                {group.headers.map((header) => {
                  const sorted = header.column.getIsSorted();
                  return (
                    <TableHead
                      key={header.id}
                      aria-sort={
                        sorted === 'asc'
                          ? 'ascending'
                          : sorted === 'desc'
                            ? 'descending'
                            : 'none'
                      }
                      className={cn(
                        header.column.id === 'email' && 'w-full',
                        header.column.id === 'role' && 'hidden sm:table-cell'
                      )}
                    >
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="-ms-2.5"
                        onClick={header.column.getToggleSortingHandler()}
                      >
                        <table.FlexRender header={header} />
                        {sorted === 'asc' ? (
                          <ArrowUp aria-hidden="true" />
                        ) : sorted === 'desc' ? (
                          <ArrowDown aria-hidden="true" />
                        ) : (
                          <ArrowUpDown
                            aria-hidden="true"
                            className="text-muted-foreground"
                          />
                        )}
                      </Button>
                    </TableHead>
                  );
                })}
                <TableHead>
                  <span className="sr-only">Actions</span>
                  <span aria-hidden="true" className="block w-20" />
                </TableHead>
              </TableRow>
            ))}
          </TableHeader>
          <TableBody ref={body}>
            {before > 0 && (
              <tr aria-hidden="true">
                <td colSpan={3} className="p-0" style={{ height: before }} />
              </tr>
            )}
            {visible.map((item) => {
              const row = rows[item.index];
              const person = row.original;
              return (
                <TableRow
                  key={row.id}
                  ref={virtualizer.measureElement}
                  data-index={item.index}
                  aria-rowindex={item.index + 2}
                  className="group h-11"
                >
                  <TableCell className="w-full max-w-0">
                    <div className="flex items-center gap-2">
                      <Email email={person.email} />
                      {person.email === me && (
                        <span className="shrink-0 text-muted-foreground">
                          You
                        </span>
                      )}
                      <CopyEmail email={person.email} />
                    </div>
                    <div className="text-muted-foreground sm:hidden">
                      {roles[person.role]}
                    </div>
                  </TableCell>
                  <TableCell className="hidden text-muted-foreground sm:table-cell">
                    {roles[person.role]}
                  </TableCell>
                  <TableCell className="py-0 text-end">
                    {person.role === 'grader' && (
                      <Button
                        variant="ghost"
                        onClick={(event) => {
                          opener.current = event.currentTarget;
                          removed.current = false;
                          setRemoveError('');
                          setConfirming(person.email);
                        }}
                      >
                        Remove
                        <span className="sr-only"> {person.email}</span>
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              );
            })}
            {after > 0 && (
              <tr aria-hidden="true">
                <td colSpan={3} className="p-0" style={{ height: after }} />
              </tr>
            )}
            {filtered && !rows.length && (
              <TableRow className="hover:bg-transparent">
                <TableCell
                  colSpan={3}
                  className="py-2 whitespace-normal text-muted-foreground"
                >
                  No one matches these filters.{' '}
                  <Button
                    variant="link"
                    onClick={() => {
                      table.resetGlobalFilter();
                      table.resetColumnFilters();
                    }}
                  >
                    Clear filters
                  </Button>
                </TableCell>
              </TableRow>
            )}
            {!filtered && !people.graders.length && (
              <TableRow className="hover:bg-transparent">
                <TableCell
                  colSpan={3}
                  className="py-4 whitespace-normal text-muted-foreground"
                >
                  No graders yet. Add a grader's email to let them sign in and
                  grade.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </section>

      <AlertDialog
        open={confirming !== null}
        onOpenChange={(open) => {
          if (!open && pending !== 'remove') setConfirming(null);
        }}
      >
        <AlertDialogContent
          finalFocus={() =>
            removed.current ? heading.current : opener.current
          }
        >
          <AlertDialogHeader>
            <AlertDialogTitle>Remove grader?</AlertDialogTitle>
            <AlertDialogDescription className="wrap-anywhere">
              {confirming} will be signed out right away and won't be able to
              sign back in.
            </AlertDialogDescription>
          </AlertDialogHeader>
          {removeError && <ErrorAlert error={removeError} />}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={pending === 'remove'}>
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              type="button"
              variant="destructive"
              disabled={pending === 'remove'}
              onClick={() => confirming && void remove(confirming)}
            >
              <SteadyLabel
                busy={pending === 'remove'}
                idle="Remove grader"
                working="Removing…"
              />
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
