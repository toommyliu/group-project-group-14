import { useId, useRef, useState, type FormEvent } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
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
import { Trash2 } from 'lucide-react';
import type { Staff, StaffRole } from '@/api/staff';
import { ErrorAlert } from '@/components/error-alert';
import {
  CopyButton,
  Email,
  FilterField,
  PeopleCell,
  PeopleHead,
  PeopleRow,
  PeopleTable,
  RowAction,
  SortableHead,
} from '@/components/people-table';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { SteadyLabel } from '@/components/steady-label';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Field,
  FieldDescription,
  FieldError,
  FieldLabel,
} from '@/components/ui/field';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { errorMessage } from '@/lib/error-message';

type Person = { email: string; role: StaffRole | 'admin' };

const roles: Record<Person['role'], string> = {
  admin: 'Admin',
  instructor: 'Instructor',
  grader: 'Grader',
};

const staffRoles = [
  { value: 'grader', label: 'Grader' },
  { value: 'instructor', label: 'Instructor' },
] as const;

const roleFilters = [
  { value: 'all', label: 'All roles' },
  { value: 'admin', label: 'Admins' },
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

const number = new Intl.NumberFormat();

function count(value: number, singular: string) {
  return `${number.format(value)} ${value === 1 ? singular : `${singular}s`}`;
}

export function AddStaff({
  domain,
  onAdd,
  onNotice,
}: {
  domain: string;
  onAdd: (email: string, role: StaffRole) => Promise<void>;
  onNotice: (message: string) => void;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<StaffRole>('grader');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  async function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving) return;
    const address = email.trim().toLowerCase();
    if (!address) {
      setError(`Enter one @${domain} email address.`);
      input.current?.focus();
      return;
    }
    setSaving(true);
    setError('');
    try {
      await onAdd(address, role);
      setOpen(false);
      onNotice(
        `Added ${address} as ${role === 'grader' ? 'a grader' : 'an instructor'}.`
      );
    } catch (cause) {
      setError(errorMessage(cause, 'Could not add this person. Try again.'));
      input.current?.focus();
    } finally {
      setSaving(false);
    }
  }

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next, details) => {
        if (saving) return details.cancel();
        if (next) {
          setEmail('');
          setRole('grader');
          setError('');
          onNotice('');
        }
        setOpen(next);
      }}
    >
      <AlertDialogTrigger render={<Button />}>Add staff</AlertDialogTrigger>
      <AlertDialogContent initialFocus={input}>
        <form
          noValidate
          onSubmit={(event) => void add(event)}
          className="grid gap-4"
        >
          <AlertDialogHeader>
            <AlertDialogTitle>Add staff</AlertDialogTitle>
          </AlertDialogHeader>
          <Field data-invalid={Boolean(error)}>
            <FieldLabel htmlFor={`${id}-email`}>Email</FieldLabel>
            <Input
              ref={input}
              id={`${id}-email`}
              type="email"
              inputMode="email"
              autoComplete="off"
              autoCapitalize="none"
              spellCheck={false}
              placeholder={`name@${domain}`}
              value={email}
              onChange={(event) => {
                setEmail(event.target.value);
                setError('');
              }}
              readOnly={saving}
              aria-invalid={Boolean(error) || undefined}
              aria-describedby={`${id}-hint${error ? ` ${id}-error` : ''}`}
            />
            <FieldDescription id={`${id}-hint`}>
              They sign in with their @{domain} Google account.
            </FieldDescription>
            <FieldError id={`${id}-error`}>{error}</FieldError>
          </Field>
          <Field>
            <FieldLabel htmlFor={`${id}-role`}>Role</FieldLabel>
            <Select
              items={staffRoles}
              value={role}
              onValueChange={(value) => value && setRole(value)}
              disabled={saving}
            >
              <SelectTrigger
                id={`${id}-role`}
                className="w-full"
                aria-describedby={`${id}-role-hint`}
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {staffRoles.map((item) => (
                  <SelectItem key={item.value} value={item.value}>
                    {item.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <FieldDescription id={`${id}-role-hint`}>
              Graders grade. Instructors can also manage people and course
              settings.
            </FieldDescription>
          </Field>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={saving}>Cancel</AlertDialogCancel>
            <Button type="submit" disabled={saving}>
              <SteadyLabel busy={saving} idle="Add" working="Adding…" />
            </Button>
          </AlertDialogFooter>
        </form>
      </AlertDialogContent>
    </AlertDialog>
  );
}

export function StaffAccess({
  staff,
  me,
  onChangeRole,
  onRemove,
  onNotice,
}: {
  staff: Staff;
  me?: string;
  onChangeRole: (email: string, role: StaffRole) => Promise<void>;
  onRemove: (email: string) => Promise<void>;
  onNotice: (message: string) => void;
}) {
  'use no memo';

  const id = useId();
  const [confirming, setConfirming] = useState({ open: false, email: '' });
  const [removeError, setRemoveError] = useState('');
  const [removing, setRemoving] = useState(false);
  const [changing, setChanging] = useState('');
  const heading = useRef<HTMLHeadingElement>(null);
  const removed = useRef(false);
  const opener = useRef<HTMLElement | null>(null);
  const body = useRef<HTMLDivElement>(null);

  const data: Person[] = [
    ...staff.admins.map((email) => ({ email, role: 'admin' as const })),
    ...staff.staff,
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
  const summary = (['admin', 'instructor', 'grader'] as const)
    .map(
      (role) =>
        [role, data.filter((person) => person.role === role).length] as const
    )
    .filter(([role, total]) => role !== 'admin' || total > 0)
    .map(([role, total]) => count(total, roles[role].toLowerCase()))
    .join(' · ');

  // oxlint-disable-next-line react/incompatible-library
  const virtualizer = useVirtualizer({
    count: rows.length,
    estimateSize: () => 44,
    getItemKey: (index) => rows[index].id,
    overscan: 10,
    getScrollElement: () => body.current,
  });
  const visible = virtualizer.getVirtualItems();
  const before = visible.length ? visible[0].start : 0;
  const after = visible.length
    ? virtualizer.getTotalSize() - visible.at(-1)!.end
    : 0;

  async function changeRole(email: string, role: StaffRole) {
    if (changing) return;
    setChanging(email);
    onNotice('');
    try {
      await onChangeRole(email, role);
      onNotice(
        `${email} is now ${role === 'grader' ? 'a grader' : 'an instructor'}.`
      );
    } catch (cause) {
      onNotice(errorMessage(cause, 'Could not change this role. Try again.'));
    } finally {
      setChanging('');
    }
  }

  async function remove(email: string) {
    if (removing) return;
    setRemoving(true);
    setRemoveError('');
    try {
      await onRemove(email);
      removed.current = true;
      setConfirming((current) => ({ ...current, open: false }));
      onNotice(`Removed ${email}.`);
    } catch (cause) {
      setRemoveError(
        errorMessage(cause, 'Could not remove this person. Try again.')
      );
    } finally {
      setRemoving(false);
    }
  }

  return (
    <>
      <section
        aria-labelledby={`${id}-staff`}
        className="flex min-h-0 flex-1 flex-col"
      >
        <h2 id={`${id}-staff`} ref={heading} tabIndex={-1} className="sr-only">
          Staff access
        </h2>
        <div className="flex min-h-0 flex-1 flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <FilterField
              label="Filter by email"
              value={emailFilter}
              onChange={(value) => table.setGlobalFilter(value)}
            />
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
              <span className="invisible col-start-1 row-start-1">
                {summary}
              </span>
            </p>
          </div>

          <PeopleTable
            aria-labelledby={`${id}-staff`}
            aria-rowcount={rows.length ? rows.length + 1 : undefined}
            columns="grid-cols-[minmax(0,1fr)_auto_auto]"
            bodyRef={body}
            header={
              <>
                {table.getHeaderGroups()[0].headers.map((header) => (
                  <SortableHead
                    key={header.id}
                    label={<table.FlexRender header={header} />}
                    sorted={header.column.getIsSorted()}
                    onSort={(event) =>
                      header.column.getToggleSortingHandler()?.(event)
                    }
                  />
                ))}
                <PeopleHead>
                  <span className="sr-only">Actions</span>
                  <span aria-hidden="true" className="block w-11" />
                </PeopleHead>
              </>
            }
          >
            {before > 0 && (
              <div
                aria-hidden="true"
                className="col-span-full"
                style={{ height: before }}
              />
            )}
            {visible.map((item) => {
              const row = rows[item.index];
              const person = row.original;
              const role = person.role;
              const editable = role !== 'admin' && person.email !== me;
              return (
                <PeopleRow
                  key={row.id}
                  ref={virtualizer.measureElement}
                  data-index={item.index}
                  aria-rowindex={item.index + 2}
                >
                  <PeopleCell className="py-1">
                    <div className="group/copy flex items-center gap-2">
                      <Email email={person.email} />
                      {person.email === me && (
                        <span className="shrink-0 text-muted-foreground">
                          You
                        </span>
                      )}
                      <CopyButton value={person.email} what="email" />
                    </div>
                  </PeopleCell>
                  <PeopleCell className="flex items-center py-1 text-muted-foreground">
                    {editable ? (
                      <Select
                        items={staffRoles}
                        value={role}
                        onValueChange={(value) =>
                          value &&
                          value !== role &&
                          void changeRole(person.email, value)
                        }
                        disabled={Boolean(changing)}
                      >
                        <SelectTrigger
                          size="sm"
                          aria-label={`Role for ${person.email}`}
                          className="w-28"
                        >
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {staffRoles.map((option) => (
                            <SelectItem key={option.value} value={option.value}>
                              {option.label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    ) : (
                      roles[role]
                    )}
                  </PeopleCell>
                  <PeopleCell className="flex justify-end">
                    {editable && (
                      <RowAction
                        action="Remove"
                        target={person.email}
                        onClick={(event) => {
                          opener.current = event.currentTarget;
                          removed.current = false;
                          setRemoveError('');
                          onNotice('');
                          setConfirming({ open: true, email: person.email });
                        }}
                      >
                        <Trash2 aria-hidden="true" />
                      </RowAction>
                    )}
                  </PeopleCell>
                </PeopleRow>
              );
            })}
            {after > 0 && (
              <div
                aria-hidden="true"
                className="col-span-full"
                style={{ height: after }}
              />
            )}
            {filtered && !rows.length && (
              <PeopleRow>
                <PeopleCell className="col-span-full py-2 text-muted-foreground">
                  No staff match these filters.{' '}
                  <Button
                    variant="link"
                    onClick={() => {
                      table.resetGlobalFilter();
                      table.resetColumnFilters();
                    }}
                  >
                    Clear filters
                  </Button>
                </PeopleCell>
              </PeopleRow>
            )}
            {!filtered && !staff.staff.length && (
              <PeopleRow>
                <PeopleCell className="col-span-full py-4 text-muted-foreground">
                  No instructors or graders yet.
                </PeopleCell>
              </PeopleRow>
            )}
          </PeopleTable>
        </div>
      </section>

      <AlertDialog
        open={confirming.open}
        onOpenChange={(open) => {
          if (!open && !removing)
            setConfirming((current) => ({ ...current, open: false }));
        }}
      >
        <AlertDialogContent
          finalFocus={() =>
            removed.current ? heading.current : opener.current
          }
        >
          <AlertDialogHeader>
            <AlertDialogTitle className="wrap-anywhere">
              Remove {confirming.email}?
            </AlertDialogTitle>
            <AlertDialogDescription>
              They'll lose access to this course right away.
            </AlertDialogDescription>
          </AlertDialogHeader>
          {removeError && <ErrorAlert error={removeError} />}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={removing}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              type="button"
              variant="destructive"
              disabled={removing}
              onClick={() => void remove(confirming.email)}
            >
              <SteadyLabel busy={removing} idle="Remove" working="Removing…" />
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
