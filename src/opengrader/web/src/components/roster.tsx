import { useDeferredValue, useId, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeftRight, Pencil, Trash2, UsersRound } from 'lucide-react';
import {
  rosterQuery,
  studentLabel,
  studentName,
  type Student,
} from '@/api/roster';
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
  RemoveStudentDialog,
  StudentDialog,
} from '@/components/student-dialog';
import { Button } from '@/components/ui/button';
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from '@/components/ui/empty';
import { searchable } from '@/lib/search';
import { useFolderSession } from '@/hooks/use-folder-session';
import { setPreference, usePreferences } from '@/stores/preferences';

type SortKey = 'name' | 'sid' | 'email';
type NameOrder = 'last' | 'first';

const number = new Intl.NumberFormat();
const collator = new Intl.Collator(undefined, {
  sensitivity: 'base',
  numeric: true,
});

function count(value: number) {
  return `${number.format(value)} ${value === 1 ? 'student' : 'students'}`;
}

function sortName({ first_name, last_name }: Student) {
  return [last_name, first_name].filter(Boolean).join(', ');
}

const nameOrders = {
  last: {
    header: 'Last, first name',
    swapLabel: 'Swap to first and last name',
    display: sortName,
    key: ({ first_name, last_name }: Student) => [
      last_name || first_name,
      first_name,
    ],
  },
  first: {
    header: 'First and last name',
    swapLabel: 'Swap to last, first name',
    display: studentName,
    key: ({ first_name, last_name }: Student) => [
      first_name || last_name,
      last_name,
    ],
  },
} as const;

function sortKey(student: Student, by: SortKey, order: NameOrder) {
  if (by === 'sid') return [student.sid];
  if (by === 'email') return [student.email ?? '', student.sid];
  return [...nameOrders[order].key(student), student.sid];
}

function compare(a: string[], b: string[]) {
  for (const [index, value] of a.entries()) {
    const result = collator.compare(value, b[index]);
    if (result) return result;
  }
  return 0;
}

function matches(student: Student, query: string) {
  return [
    studentName(student),
    sortName(student),
    student.sid,
    student.email ?? '',
  ]
    .map(searchable)
    .some((field) => field.includes(query));
}

export function Roster({
  initialStudents,
  onNotice,
}: {
  initialStudents: Student[];
  onNotice: (message: string) => void;
}) {
  const id = useId();
  const queryClient = useQueryClient();
  const folderRoster = useFolderSession()?.roster;
  const editable = !folderRoster || folderRoster.editable;
  const roster = useQuery({ ...rosterQuery, initialData: initialStudents });
  const students = roster.data;
  const nameOrder = usePreferences(
    (preferences) => preferences.rosterNameOrder
  );
  const [sort, setSort] = useState<{ by: SortKey; descending: boolean }>({
    by: 'name',
    descending: false,
  });
  const [filter, setFilter] = useState('');
  const query = useDeferredValue(searchable(filter));
  const shown = (
    query ? students.filter((student) => matches(student, query)) : students
  ).toSorted((a, b) => {
    const result = compare(
      sortKey(a, sort.by, nameOrder),
      sortKey(b, sort.by, nameOrder)
    );
    return sort.descending ? -result : result;
  });
  const [editing, setEditing] = useState<{
    open: boolean;
    student: Student | null;
    key: number;
  }>({ open: false, student: null, key: 0 });
  const [removing, setRemoving] = useState<Student | null>(null);
  const opener = useRef<HTMLElement | null>(null);
  const removed = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);

  function sortBy(by: SortKey) {
    setSort((current) => ({
      by,
      descending: current.by === by && !current.descending,
    }));
  }

  function sorted(by: SortKey) {
    if (sort.by !== by) return false;
    return sort.descending ? 'desc' : 'asc';
  }

  function edit(student: Student, trigger: HTMLElement) {
    opener.current = trigger;
    onNotice('');
    setEditing(({ key }) => ({ open: true, student, key: key + 1 }));
  }

  function remove(student: Student, trigger: HTMLElement) {
    opener.current = trigger;
    removed.current = false;
    onNotice('');
    setRemoving(student);
  }

  return (
    <section
      aria-labelledby={`${id}-students`}
      className="flex min-h-0 flex-1 flex-col gap-3"
    >
      <h2 id={`${id}-students`} ref={heading} tabIndex={-1} className="sr-only">
        Students
      </h2>

      {roster.isRefetchError && (
        <ErrorAlert
          title="The roster could not be refreshed."
          error={roster.error}
        >
          <Button variant="outline" onClick={() => void roster.refetch()}>
            Retry
          </Button>
        </ErrorAlert>
      )}

      {students.length === 0 ? (
        <Empty className="border">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <UsersRound />
            </EmptyMedia>
            <EmptyTitle>No students yet</EmptyTitle>
            <EmptyDescription>
              {folderRoster
                ? 'The roster file in the class folder has no students yet.'
                : 'Use Add students to add them one at a time or import a CSV file.'}
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <FilterField
              label="Filter by name, SID, or email"
              value={filter}
              onChange={setFilter}
            />
            <p
              role="status"
              className="ms-auto text-sm text-muted-foreground tabular-nums"
            >
              {query
                ? `Showing ${number.format(shown.length)} of ${count(students.length)}`
                : count(students.length)}
            </p>
          </div>

          <PeopleTable
            aria-labelledby={`${id}-students`}
            columns="grid-cols-[minmax(0,1fr)_auto] sm:grid-cols-[minmax(0,1fr)_auto_auto] md:grid-cols-[minmax(0,1fr)_auto_minmax(0,max-content)_auto]"
            header={
              <>
                <SortableHead
                  label={nameOrders[nameOrder].header}
                  sorted={sorted('name')}
                  onSort={() => sortBy('name')}
                >
                  <Button
                    type="button"
                    variant="outline"
                    size="xs"
                    aria-label={nameOrders[nameOrder].swapLabel}
                    onClick={() =>
                      setPreference(
                        'rosterNameOrder',
                        nameOrder === 'last' ? 'first' : 'last'
                      )
                    }
                  >
                    <ArrowLeftRight aria-hidden="true" />
                    Swap
                  </Button>
                </SortableHead>
                <SortableHead
                  label="SID"
                  sorted={sorted('sid')}
                  onSort={() => sortBy('sid')}
                  className="hidden sm:flex"
                />
                <SortableHead
                  label="Email"
                  sorted={sorted('email')}
                  onSort={() => sortBy('email')}
                  className="hidden md:flex"
                />
                <PeopleHead>
                  <span className="sr-only">Actions</span>
                  <span aria-hidden="true" className="block w-22" />
                </PeopleHead>
              </>
            }
          >
            {shown.map((student) => {
              const name = nameOrders[nameOrder].display(student);
              return (
                <PeopleRow key={student.id}>
                  <PeopleCell className="py-1">
                    <div className="group/copy flex items-center gap-1">
                      <span className="truncate" title={name}>
                        {name}
                      </span>
                      <CopyButton value={name} what="name" />
                    </div>
                    <div className="group/copy flex items-center gap-1 text-muted-foreground md:hidden">
                      <span className="shrink-0 tabular-nums sm:hidden">
                        {student.sid}
                        {student.email && ' · '}
                      </span>
                      {student.email && (
                        <>
                          <Email email={student.email} />
                          <CopyButton value={student.email} what="email" />
                        </>
                      )}
                    </div>
                  </PeopleCell>
                  <PeopleCell className="hidden sm:block">
                    <div className="group/copy flex items-center gap-1">
                      <span className="tabular-nums">{student.sid}</span>
                      <CopyButton value={student.sid} what="SID" />
                    </div>
                  </PeopleCell>
                  <PeopleCell className="hidden text-muted-foreground md:block">
                    {student.email && (
                      <div className="group/copy flex max-w-72 items-center gap-1">
                        <Email email={student.email} />
                        <CopyButton value={student.email} what="email" />
                      </div>
                    )}
                  </PeopleCell>
                  <PeopleCell className="flex justify-end">
                    {editable && (
                      <>
                        <RowAction
                          action="Edit"
                          target={studentLabel(student)}
                          onClick={(event) =>
                            edit(student, event.currentTarget)
                          }
                        >
                          <Pencil aria-hidden="true" />
                        </RowAction>
                        <RowAction
                          action="Remove"
                          target={studentLabel(student)}
                          onClick={(event) =>
                            remove(student, event.currentTarget)
                          }
                        >
                          <Trash2 aria-hidden="true" />
                        </RowAction>
                      </>
                    )}
                  </PeopleCell>
                </PeopleRow>
              );
            })}
            {shown.length === 0 && (
              <PeopleRow>
                <PeopleCell className="col-span-full py-2 text-muted-foreground">
                  No students match “{filter.trim()}”.{' '}
                  <Button variant="link" onClick={() => setFilter('')}>
                    Clear filter
                  </Button>
                </PeopleCell>
              </PeopleRow>
            )}
          </PeopleTable>
        </>
      )}

      <StudentDialog
        key={editing.key}
        open={editing.open}
        student={editing.student}
        onOpenChange={(open) => setEditing((current) => ({ ...current, open }))}
        onDone={(message) => {
          onNotice(message);
          setEditing((current) => ({ ...current, open: false }));
        }}
        finalFocus={() => opener.current}
      />
      <RemoveStudentDialog
        student={removing}
        onClose={() => setRemoving(null)}
        onRemoved={async (student) => {
          await queryClient.invalidateQueries({
            queryKey: rosterQuery.queryKey,
          });
          removed.current = true;
          setRemoving(null);
          onNotice(`Removed ${studentLabel(student)}.`);
        }}
        finalFocus={() => (removed.current ? heading.current : opener.current)}
      />
    </section>
  );
}
