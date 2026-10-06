import {
  Fragment,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { Link } from '@tanstack/react-router';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowRight,
  Check,
  ChevronDown,
  CircleCheck,
  FileText,
  FileUp,
  LoaderCircle,
  PenLine,
  SearchX,
  TriangleAlert,
  UserCheck,
} from 'lucide-react';
import { cn } from 'cn';
import { workspaceUrl } from '@/api/client';
import {
  matchRoster,
  rosterQuery,
  setStudent,
  studentName,
  type Student,
} from '@/api/roster';
import { submissionsQuery, type Submission } from '@/api/queries';
import { outlineQuery } from '@/api/outline';
import {
  scanBusy,
  scansQuery,
  studentUploads,
  uploadScan,
  type StudentUpload,
} from '@/api/scans';
import { submissionsRoute } from '@/router';
import { useFolderSession } from '@/hooks/use-folder-session';
import { errorMessage } from '@/lib/error-message';
import { searchable } from '@/lib/search';
import { PageHeading } from '@/components/page-heading';
import { FilterTabs } from '@/components/filter-tabs';
import { TableEmpty } from '@/components/table-empty';
import { ErrorAlert } from '@/components/error-alert';
import {
  FilterField,
  PeopleCell,
  PeopleHead,
  PeopleRow,
  PeopleTable,
} from '@/components/people-table';
import { Button, buttonVariants } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Popover,
  PopoverContent,
  PopoverTitle,
  PopoverTrigger,
} from '@/components/ui/popover';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import {
  Combobox,
  ComboboxCollection,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxGroup,
  ComboboxInput,
  ComboboxItem,
  ComboboxLabel,
  ComboboxList,
  ComboboxTrigger,
} from '@/components/ui/combobox';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from '@/components/ui/empty';

type Identity = Pick<Submission, 'student_name' | 'student_id'>;
type Filter = 'all' | 'attention' | 'automatic' | 'missing';
type Problem =
  | { kind: 'unnamed' }
  | { kind: 'duplicate'; others: number[] }
  | { kind: 'off-roster' };
type Row = { submission: Submission; problem: Problem | null };

const collator = new Intl.Collator(undefined, { sensitivity: 'base' });
const byName = (a: Student, b: Student) =>
  collator.compare(studentName(a), studentName(b));

export default function SubmissionsPage() {
  const loaded = submissionsRoute.useLoaderData();
  const { assignment } = loaded;
  const query = useQuery({
    ...submissionsQuery(assignment.id),
    initialData: loaded.submissions,
  });
  // Graders cannot list the roster; matching still works for them on the server.
  const roster = useQuery({
    ...rosterQuery,
    initialData: loaded.students ?? undefined,
    enabled: loaded.students !== null,
  });
  const client = useQueryClient();
  const folderSession = useFolderSession();
  const [filter, setFilter] = useState<Filter>(() =>
    loaded.submissions.length === 0 && loaded.students?.length
      ? 'missing'
      : 'all'
  );
  const [kept, setKept] = useState<number[]>([]);
  const [keptStudents, setKeptStudents] = useState<string[]>([]);
  const [text, setText] = useState('');
  const search = useDeferredValue(searchable(text));
  const [matching, setMatching] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');

  const items = query.data;
  const students = useMemo(
    () => (roster.data ?? []).toSorted(byName),
    [roster.data]
  );
  const bySid = new Map(students.map((student) => [student.sid, student]));
  const holders = new Map<string, number[]>();
  for (const item of items) {
    if (item.student_id)
      holders.set(item.student_id, [
        ...(holders.get(item.student_id) ?? []),
        item.id,
      ]);
  }
  const rows: Row[] = items.map((submission) => {
    const others = (holders.get(submission.student_id) ?? []).filter(
      (id) => id !== submission.id
    );
    const problem: Problem | null = !submission.student_name.trim()
      ? { kind: 'unnamed' }
      : others.length
        ? { kind: 'duplicate', others }
        : students.length && !bySid.has(submission.student_id)
          ? { kind: 'off-roster' }
          : null;
    return { submission, problem };
  });
  const missing = students.filter((student) => !holders.has(student.sid));
  const unnamed = rows.filter((row) => row.problem?.kind === 'unnamed').length;
  const counts = {
    all: rows.length,
    attention: rows.filter((row) => row.problem).length,
    automatic: rows.filter((row) => row.submission.match_percent !== null)
      .length,
    missing: missing.length,
  };
  const inFilter = (row: Row) =>
    filter === 'all' ||
    kept.includes(row.submission.id) ||
    (filter === 'attention'
      ? row.problem !== null
      : row.submission.match_percent !== null);
  const found = (...fields: string[]) =>
    !search || fields.some((field) => searchable(field).includes(search));
  const shownRows =
    filter === 'missing'
      ? []
      : rows.filter(
          (row) =>
            inFilter(row) &&
            found(
              row.submission.student_name,
              row.submission.student_id,
              `#${row.submission.id}`
            )
        );
  const shownMissing =
    filter === 'missing'
      ? students.filter(
          (student) =>
            (!holders.has(student.sid) || keptStudents.includes(student.sid)) &&
            found(studentName(student), student.sid)
        )
      : [];
  const uploadable = filter === 'missing' && !folderSession;
  const scans = useQuery({
    ...scansQuery(assignment.id),
    enabled: uploadable,
  });
  const outline = useQuery({
    ...outlineQuery(assignment.id),
    enabled: uploadable,
  });
  const uploads = studentUploads(scans.data?.batches ?? []);
  const byId = new Map(rows.map((row) => [row.submission.id, row]));
  const unpublished = [...uploads.values()].some(
    ({ group }) =>
      group.submission_id !== null && !byId.has(group.submission_id)
  );
  useEffect(() => {
    if (unpublished)
      void client.invalidateQueries({
        queryKey: submissionsQuery(assignment.id).queryKey,
      });
  }, [unpublished, client, assignment.id]);
  const shownIds = new Set<number>();
  const missingRows = shownMissing.map((student) => {
    const latest = uploads.get(student.sid);
    const uploaded =
      latest?.group.submission_id != null
        ? byId.get(latest.group.submission_id)
        : undefined;
    const kept = !keptStudents.includes(student.sid)
      ? undefined
      : latest
        ? uploaded
        : rows.find((item) => item.submission.student_id === student.sid);
    const row = kept && !shownIds.has(kept.submission.id) ? kept : undefined;
    if (row) shownIds.add(row.submission.id);
    const upload = uploaded ? undefined : latest;
    return {
      student,
      row,
      upload,
      missing:
        !holders.has(student.sid) || upload?.group.submission_id === null,
    };
  });
  const [refreshTitle, refreshError] = query.error
    ? ['Could not refresh submissions', query.error]
    : roster.error
      ? ['Could not refresh the roster', roster.error]
      : scans.error
        ? ['Could not check uploads', scans.error]
        : outline.error
          ? ['Could not load the outline', outline.error]
          : [undefined, null];
  const versions = new Set(items.map((item) => item.version).filter(Boolean));
  const hasVersions = versions.size > 1;

  async function match() {
    setMatching(true);
    setError('');
    setNotice('');
    try {
      const result = await matchRoster(assignment.id);
      await client.invalidateQueries({
        queryKey: submissionsQuery(assignment.id).queryKey,
      });
      setNotice(
        [
          `Matched ${result.matched} ${result.matched === 1 ? 'submission' : 'submissions'}`,
          result.ambiguous > 0 && `${result.ambiguous} need you to pick`,
          result.unmatched > 0 && `${result.unmatched} not found`,
        ]
          .filter(Boolean)
          .join(' · ')
      );
    } catch (cause) {
      setError(errorMessage(cause, 'Could not match names. Try again.'));
    } finally {
      setMatching(false);
    }
  }

  function keep(submissionId: number) {
    if (filter !== 'all')
      setKept((current) =>
        current.includes(submissionId) ? current : [...current, submissionId]
      );
  }

  return (
    <>
      <PageHeading
        title="Submissions"
        actions={
          items.length > 0 && (
            <div className="flex flex-wrap items-center justify-end gap-x-4 gap-y-2">
              <p role="status" className="text-sm text-muted-foreground">
                {notice}
              </p>
              {unnamed > 0 && !folderSession && (
                <Button
                  variant="outline"
                  disabled={matching}
                  onClick={() => void match()}
                >
                  {matching ? (
                    <LoaderCircle
                      className="motion-safe:animate-spin"
                      aria-hidden="true"
                    />
                  ) : (
                    <UserCheck aria-hidden="true" />
                  )}
                  {matching ? 'Matching…' : 'Match to roster'}
                </Button>
              )}
              <Button
                render={
                  <Link
                    to="/assignments/$assignmentId/grade"
                    params={{ assignmentId: assignment.id }}
                  />
                }
              >
                Start grading
                <ArrowRight aria-hidden="true" />
              </Button>
            </div>
          )
        }
      />
      {(refreshError || error) && (
        <ErrorAlert
          className="mb-4"
          title={error ? undefined : refreshTitle}
          error={error || refreshError}
        />
      )}
      {items.length === 0 && (students.length === 0 || folderSession) ? (
        <Empty className="border py-16">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <FileUp />
            </EmptyMedia>
            <EmptyTitle>No submissions yet</EmptyTitle>
            <EmptyDescription>
              Upload and split scans first. Each submission then shows the name
              its student wrote, matched to the roster when one is set up.
            </EmptyDescription>
          </EmptyHeader>
          {!folderSession && (
            <EmptyContent>
              <Button
                render={
                  <Link
                    to="/assignments/$assignmentId/scans"
                    params={{ assignmentId: assignment.id }}
                  />
                }
              >
                Go to scans
              </Button>
            </EmptyContent>
          )}
        </Empty>
      ) : (
        <section
          aria-label="Submissions"
          className="@container flex min-h-0 flex-1 flex-col gap-3"
        >
          <div className="flex flex-wrap items-center gap-2">
            <FilterTabs
              label="Show"
              value={filter}
              onChange={(next) => {
                setFilter(next);
                setKept([]);
                setKeptStudents([]);
              }}
              options={[
                { value: 'all', label: 'All', count: counts.all },
                {
                  value: 'attention',
                  label: 'Needs attention',
                  count: counts.attention,
                },
                ...(students.length > 0 || counts.automatic > 0
                  ? [
                      {
                        value: 'automatic' as const,
                        label: 'Matched automatically',
                        count: counts.automatic,
                      },
                    ]
                  : []),
                ...(students.length > 0
                  ? [
                      {
                        value: 'missing' as const,
                        label: 'No submission',
                        count: counts.missing,
                      },
                    ]
                  : []),
              ]}
            />
            <FilterField
              label="Filter by name or SID"
              value={text}
              onChange={setText}
              className="sm:w-full @4xl:ms-auto @4xl:w-64"
            />
          </div>

          <PeopleTable
            aria-label="Submissions"
            columns={
              hasVersions
                ? 'grid-cols-1 @xl:grid-cols-[auto_minmax(12rem,1fr)_auto] @2xl:grid-cols-[auto_auto_minmax(12rem,1fr)_auto_auto] @3xl:grid-cols-[auto_auto_minmax(12rem,1fr)_auto_auto_auto]'
                : 'grid-cols-1 @xl:grid-cols-[auto_minmax(12rem,1fr)_auto] @2xl:grid-cols-[auto_auto_minmax(12rem,1fr)_auto_auto]'
            }
            headerClassName="@max-xl:sr-only"
            header={
              <>
                <PeopleHead className="hidden @2xl:flex">#</PeopleHead>
                <PeopleHead>Written on the page</PeopleHead>
                <PeopleHead>Student</PeopleHead>
                {hasVersions && (
                  <PeopleHead className="hidden @3xl:flex">Version</PeopleHead>
                )}
                <PeopleHead className="hidden justify-end @2xl:flex">
                  Graded
                </PeopleHead>
                <PeopleHead>
                  <span className="sr-only">Actions</span>
                  <span aria-hidden="true" className="block w-33" />
                </PeopleHead>
              </>
            }
          >
            {shownRows.map((row) => (
              <SubmissionRow
                key={row.submission.id}
                assignmentId={assignment.id}
                row={row}
                roster={students}
                holders={holders}
                hasVersions={hasVersions}
                editable={!folderSession}
                onEdit={() => keep(row.submission.id)}
              />
            ))}
            {missingRows.map(({ student, row, missing, upload }) => {
              return (
                <Fragment key={student.id}>
                  {row && (
                    <SubmissionRow
                      assignmentId={assignment.id}
                      row={row}
                      roster={students}
                      holders={holders}
                      hasVersions={hasVersions}
                      editable={!folderSession}
                      onEdit={() => keep(row.submission.id)}
                    />
                  )}
                  {missing && (
                    <MissingRow
                      assignmentId={assignment.id}
                      student={student}
                      hasVersions={hasVersions}
                      versions={
                        uploadable ? outline.data?.versions_with_regions : null
                      }
                      upload={upload}
                      onUploaded={() =>
                        setKeptStudents((current) => [...current, student.sid])
                      }
                    />
                  )}
                </Fragment>
              );
            })}
            {shownRows.length === 0 &&
              shownMissing.length === 0 &&
              (items.length === 0 && filter !== 'missing' ? (
                <TableEmpty
                  icon={<FileUp />}
                  title="No submissions yet"
                  description="Upload a class scan on the Scans page, or upload one student's PDF from No submission."
                  action={
                    <Button
                      variant="outline"
                      onClick={() => setFilter('missing')}
                    >
                      Show students without a submission
                    </Button>
                  }
                />
              ) : search ? (
                <TableEmpty
                  icon={<SearchX />}
                  title={`Nothing matches “${text.trim()}”`}
                  description="Search by a student's name, student ID, or submission number."
                  action={
                    <Button variant="outline" onClick={() => setText('')}>
                      Clear search
                    </Button>
                  }
                />
              ) : (
                <TableEmpty
                  icon={<CircleCheck />}
                  title={
                    filter === 'missing'
                      ? 'Every student on the roster has a submission'
                      : filter === 'automatic'
                        ? 'No submission was matched automatically'
                        : 'Every submission has a student'
                  }
                  action={
                    <Button variant="outline" onClick={() => setFilter('all')}>
                      Show all submissions
                    </Button>
                  }
                />
              ))}
          </PeopleTable>
        </section>
      )}
    </>
  );
}

function SubmissionRow({
  assignmentId,
  row: { submission, problem },
  roster,
  holders,
  hasVersions,
  editable,
  onEdit,
}: {
  assignmentId: string;
  row: Row;
  roster: Student[];
  holders: Map<string, number[]>;
  hasVersions: boolean;
  editable: boolean;
  onEdit: () => void;
}) {
  const scores = Object.values(submission.question_scores);
  const graded = scores.filter((score) => score !== null).length;
  return (
    <PeopleRow>
      <PeopleCell className="hidden text-muted-foreground tabular-nums @2xl:block">
        {submission.id}
      </PeopleCell>
      <PeopleCell className="py-2">
        <Handwriting submission={submission} />
      </PeopleCell>
      <PeopleCell>
        {!editable ? (
          <div className="grid">
            <span className="truncate font-medium">
              {submission.student_name || 'No student'}
            </span>
            <span className="text-xs text-muted-foreground tabular-nums">
              {submission.student_id}
            </span>
          </div>
        ) : (
          <StudentCell
            assignmentId={assignmentId}
            submission={submission}
            problem={problem}
            roster={roster}
            holders={holders}
            onEdit={onEdit}
          />
        )}
      </PeopleCell>
      {hasVersions && (
        <PeopleCell className="hidden @3xl:block">
          {submission.version ?? '—'}
        </PeopleCell>
      )}
      <PeopleCell
        className={cn(
          'hidden text-end tabular-nums @2xl:block',
          graded < scores.length && 'text-muted-foreground'
        )}
      >
        {graded} of {scores.length}
      </PeopleCell>
      <PeopleCell className="flex items-center justify-end pb-2 @xl:pb-0">
        <span className="me-auto text-xs text-muted-foreground tabular-nums @xl:hidden">
          #{submission.id} · {graded} of {scores.length} graded
        </span>
        <Tooltip>
          <TooltipTrigger
            render={
              <a
                href={workspaceUrl(
                  `/api/assignments/${encodeURIComponent(assignmentId)}/submissions/${submission.id}/pdf`
                )}
                target="_blank"
                rel="noreferrer"
                aria-label={`Open the PDF of submission ${submission.id}`}
                className={cn(
                  buttonVariants({
                    variant: 'ghost',
                    size: 'icon',
                  }),
                  'size-11'
                )}
              />
            }
          >
            <FileText aria-hidden="true" />
          </TooltipTrigger>
          <TooltipContent>Open the PDF</TooltipContent>
        </Tooltip>
        <Button
          variant="ghost"
          aria-label={`Grade submission ${submission.id}`}
          render={
            <Link
              to="/assignments/$assignmentId/grade"
              params={{ assignmentId }}
              search={{ submission: submission.id }}
            />
          }
        >
          Grade
          <ArrowRight aria-hidden="true" />
        </Button>
      </PeopleCell>
    </PeopleRow>
  );
}

function MissingRow({
  assignmentId,
  student,
  hasVersions,
  versions,
  upload: pending,
  onUploaded,
}: {
  assignmentId: string;
  student: Student;
  hasVersions: boolean;
  versions: string[] | null | undefined;
  upload: StudentUpload | undefined;
  onUploaded: () => void;
}) {
  const client = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const version = useRef('');
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');
  const name = studentName(student);
  const failed =
    pending?.group.submission_id === null && pending.batch.status === 'failed'
      ? pending.group.error || pending.batch.message
      : '';
  const busy =
    uploading ||
    (pending !== undefined &&
      (pending.group.submission_id !== null || scanBusy(pending.batch)));

  async function upload(file: File) {
    setUploading(true);
    setError('');
    try {
      await uploadScan(assignmentId, file, undefined, {
        sid: student.sid,
        version: version.current,
      });
      onUploaded();
      await client.invalidateQueries({
        queryKey: scansQuery(assignmentId).queryKey,
      });
    } catch (cause) {
      setError(errorMessage(cause, 'Could not upload this PDF. Try again.'));
    } finally {
      setUploading(false);
    }
  }

  function choose(next: string) {
    version.current = next;
    input.current?.click();
  }

  const label = `Upload a submission for ${name}`;
  return (
    <PeopleRow>
      <PeopleCell className="hidden @2xl:block" />
      <PeopleCell className="py-2">
        <div
          className={cn(
            'grid h-[4.625rem] w-full max-w-72 place-items-center border border-dashed px-2 text-center text-xs @xl:w-40 @2xl:w-56',
            error || failed ? 'text-destructive' : 'text-muted-foreground'
          )}
        >
          <span
            role={error || failed ? 'alert' : undefined}
            className="line-clamp-3"
          >
            {error || failed || 'No submission'}
          </span>
        </div>
      </PeopleCell>
      <PeopleCell className="grid">
        <span className="truncate font-medium">{name}</span>
        <span className="text-xs text-muted-foreground tabular-nums">
          {student.sid}
        </span>
      </PeopleCell>
      {hasVersions && <PeopleCell className="hidden @3xl:block" />}
      <PeopleCell className="hidden @2xl:block" />
      <PeopleCell className="flex items-center justify-end pb-2 @xl:pb-0">
        <input
          ref={input}
          type="file"
          accept="application/pdf,.pdf"
          hidden
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = '';
            if (file) void upload(file);
          }}
        />
        {busy ? (
          <span
            role="status"
            className="flex min-h-11 items-center gap-2 px-2.5 text-sm text-muted-foreground"
          >
            <LoaderCircle
              className="size-4 motion-safe:animate-spin"
              aria-hidden="true"
            />
            {uploading ? 'Uploading…' : 'Processing…'}
          </span>
        ) : failed ? (
          <Button
            variant="ghost"
            render={
              <Link
                to="/assignments/$assignmentId/scans"
                params={{ assignmentId }}
              />
            }
          >
            Open scans
            <ArrowRight aria-hidden="true" />
          </Button>
        ) : versions === null ? null : versions?.length === 0 ? (
          <Button
            variant="ghost"
            render={
              <Link
                to="/assignments/$assignmentId/outline"
                params={{ assignmentId }}
              />
            }
          >
            Draw the outline
            <ArrowRight aria-hidden="true" />
          </Button>
        ) : versions && versions.length > 1 ? (
          <DropdownMenu>
            <DropdownMenuTrigger
              render={<Button variant="ghost" aria-label={label} />}
            >
              <FileUp aria-hidden="true" />
              Upload
              <ChevronDown aria-hidden="true" />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-44">
              <DropdownMenuGroup>
                <DropdownMenuLabel>Version taken</DropdownMenuLabel>
                {versions.map((item) => (
                  <DropdownMenuItem key={item} onClick={() => choose(item)}>
                    Version {item}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuGroup>
            </DropdownMenuContent>
          </DropdownMenu>
        ) : (
          <Button
            variant="ghost"
            aria-label={label}
            disabled={!versions?.length}
            onClick={() => choose(versions?.[0] ?? '')}
          >
            <FileUp aria-hidden="true" />
            Upload
          </Button>
        )}
      </PeopleCell>
    </PeopleRow>
  );
}

function Handwriting({ submission }: { submission: Submission }) {
  const crops = [
    { url: submission.name_image_url, label: 'Name' },
    { url: submission.id_image_url, label: 'Student ID' },
  ].filter((crop): crop is { url: string; label: string } => !!crop.url);
  if (crops.length === 0) {
    return (
      <div className="grid h-[4.625rem] w-full max-w-72 place-items-center border border-dashed px-2 text-center text-xs text-muted-foreground @xl:w-40 @2xl:w-56">
        The outline has no name box
      </div>
    );
  }
  return (
    <Popover>
      <PopoverTrigger
        render={
          <button
            type="button"
            aria-label={`Enlarge what submission ${submission.id} wrote`}
            className="block w-full max-w-72 cursor-zoom-in divide-y divide-neutral-200 border bg-white outline-none focus-visible:ring-2 focus-visible:ring-ring @xl:w-40 @2xl:w-56"
          />
        }
      >
        {crops.map((crop) => (
          <img
            key={crop.label}
            src={workspaceUrl(crop.url)}
            alt=""
            className="h-9 w-full object-contain object-left"
          />
        ))}
      </PopoverTrigger>
      <PopoverContent
        align="start"
        side="bottom"
        className="w-[36rem] divide-y divide-neutral-200 bg-white p-0"
      >
        {crops.map((crop) => (
          <img
            key={crop.label}
            src={workspaceUrl(crop.url)}
            alt={`${crop.label} written on submission ${submission.id}`}
            className="max-h-48 w-full object-contain object-left"
          />
        ))}
      </PopoverContent>
    </Popover>
  );
}

function StudentCell({
  assignmentId,
  submission,
  problem,
  roster,
  holders,
  onEdit,
}: {
  assignmentId: string;
  submission: Submission;
  problem: Problem | null;
  roster: Student[];
  holders: Map<string, number[]>;
  onEdit: () => void;
}) {
  const client = useQueryClient();
  const [draft, setDraft] = useState<Identity | null>(null);
  const [error, setError] = useState('');
  const [picking, setPicking] = useState(false);
  const [entering, setEntering] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  const queue = useRef(Promise.resolve());
  const shown = draft ?? submission;
  const selected = roster.find((student) => student.sid === shown.student_id);
  const name = shown.student_name.trim();

  function save(identity: Identity) {
    setDraft(identity);
    setError('');
    onEdit();
    queue.current = queue.current.then(async () => {
      try {
        const updated = await setStudent(assignmentId, submission.id, identity);
        client.setQueryData(
          submissionsQuery(assignmentId).queryKey,
          (previous) =>
            previous?.map((item) => (item.id === updated.id ? updated : item))
        );
      } catch (cause) {
        setError(errorMessage(cause, 'Could not save. Try again.'));
      } finally {
        setDraft((current) => (current === identity ? null : current));
      }
    });
  }

  const label = (
    <>
      <span className="grid min-w-0 flex-1 text-start">
        {name ? (
          <>
            <span className="truncate font-medium" title={name}>
              {name}
            </span>
            <span className="truncate text-xs text-muted-foreground tabular-nums">
              {shown.student_id || 'No student ID'}
            </span>
          </>
        ) : (
          <span className="text-muted-foreground">
            {roster.length ? 'Choose a student' : 'Enter a name'}
          </span>
        )}
      </span>
      {draft && (
        <LoaderCircle
          className="text-muted-foreground motion-safe:animate-spin"
          aria-label="Saving"
        />
      )}
    </>
  );
  const buttonClass = cn(
    '-ms-2 h-auto min-h-11 w-full max-w-80 justify-between gap-2 px-2 py-1 font-normal',
    !name && 'ms-0 border-dashed'
  );
  const form = (
    <PopoverContent
      align="start"
      side="bottom"
      anchor={button}
      finalFocus={button}
      className="w-72"
    >
      <NameForm
        submissionId={submission.id}
        initial={shown}
        onCancel={() => setEntering(false)}
        onSave={(identity) => {
          setEntering(false);
          save(identity);
        }}
      />
    </PopoverContent>
  );

  return (
    <div className="grid justify-items-start gap-0.5">
      {roster.length ? (
        <>
          <StudentPicker
            open={picking}
            onOpenChange={setPicking}
            submission={submission}
            selected={selected ?? null}
            roster={roster}
            holders={holders}
            onPick={(student) =>
              save({
                student_name: studentName(student),
                student_id: student.sid,
              })
            }
            onEnterName={() => {
              setPicking(false);
              setEntering(true);
            }}
            trigger={
              <Button
                ref={button}
                variant={name ? 'ghost' : 'outline'}
                className={buttonClass}
              />
            }
          >
            {label}
          </StudentPicker>
          <Popover open={entering} onOpenChange={setEntering}>
            {form}
          </Popover>
        </>
      ) : (
        <Popover open={entering} onOpenChange={setEntering}>
          <PopoverTrigger
            render={
              <Button
                ref={button}
                variant={name ? 'ghost' : 'outline'}
                aria-label={`Student for submission ${submission.id}${name ? `: ${name}` : ''}`}
                className={buttonClass}
              />
            }
          >
            {label}
            <PenLine aria-hidden="true" className="text-muted-foreground" />
          </PopoverTrigger>
          {form}
        </Popover>
      )}
      {error ? (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      ) : (
        !draft && <StatusLine submission={submission} problem={problem} />
      )}
    </div>
  );
}

function StatusLine({
  submission,
  problem,
}: {
  submission: Submission;
  problem: Problem | null;
}) {
  const warning = 'text-amber-700 dark:text-amber-500';
  if (problem?.kind === 'duplicate')
    return (
      <p className={cn('flex items-center gap-1 text-xs', warning)}>
        <TriangleAlert aria-hidden="true" className="size-3.5" />
        Same student as {problem.others.map((id) => `#${id}`).join(', ')}
      </p>
    );
  if (problem?.kind === 'off-roster')
    return (
      <p className={cn('flex items-center gap-1 text-xs', warning)}>
        <TriangleAlert aria-hidden="true" className="size-3.5" />
        Not on the roster
      </p>
    );
  if (problem?.kind === 'unnamed' && submission.candidates.length > 0)
    return (
      <p className="text-xs text-muted-foreground">
        {submission.candidates.length} close matches on the roster
      </p>
    );
  if (submission.match_percent !== null)
    return (
      <p className="flex items-center gap-1 text-xs text-muted-foreground">
        <Check aria-hidden="true" className="size-3.5" />
        Matched automatically · {Math.round(submission.match_percent)}%
      </p>
    );
  return null;
}

function StudentPicker({
  open,
  onOpenChange,
  submission,
  selected,
  roster,
  holders,
  onPick,
  onEnterName,
  trigger,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  submission: Submission;
  selected: Student | null;
  roster: Student[];
  holders: Map<string, number[]>;
  onPick: (student: Student) => void;
  onEnterName: () => void;
  trigger: React.ReactElement;
  children: React.ReactNode;
}) {
  const close = new Set(submission.candidates.map((item) => item.student_id));
  const others = roster.filter((student) => student.id !== selected?.id);
  const groups = [
    { value: 'Current', items: selected ? [selected] : [] },
    {
      value: 'Close matches',
      items: others.filter((student) => close.has(student.sid)),
    },
    {
      value: 'No submission yet',
      items: others.filter(
        (student) => !close.has(student.sid) && !holders.has(student.sid)
      ),
    },
    {
      value: 'On other submissions',
      items: others.filter(
        (student) => !close.has(student.sid) && holders.has(student.sid)
      ),
    },
  ].filter((group) => group.items.length > 0);

  return (
    <Combobox
      open={open}
      onOpenChange={onOpenChange}
      items={groups}
      value={selected}
      itemToStringLabel={studentName}
      isItemEqualToValue={(item: Student, value: Student) =>
        item.id === value.id
      }
      filter={(student: Student, query: string) => {
        const text = searchable(query);
        return (
          !text ||
          searchable(studentName(student)).includes(text) ||
          searchable(student.sid).includes(text)
        );
      }}
      onValueChange={(student: Student | null) => {
        if (student && student.id !== selected?.id) onPick(student);
      }}
    >
      <ComboboxTrigger
        aria-label={`Student for submission ${submission.id}${selected ? `: ${studentName(selected)}` : ''}`}
        render={trigger}
      >
        {children}
      </ComboboxTrigger>
      <ComboboxContent className="min-w-80">
        <ComboboxInput
          showTrigger={false}
          placeholder="Search by name or SID"
        />
        <ComboboxEmpty>No one on the roster matches.</ComboboxEmpty>
        <ComboboxList>
          {(group: { value: string; items: Student[] }) => (
            <ComboboxGroup key={group.value} items={group.items}>
              <ComboboxLabel>{group.value}</ComboboxLabel>
              <ComboboxCollection>
                {(student: Student) => {
                  const owners = (holders.get(student.sid) ?? []).filter(
                    (id) => id !== submission.id
                  );
                  return (
                    <ComboboxItem key={student.id} value={student}>
                      <span className="min-w-0 truncate">
                        {studentName(student)}
                      </span>
                      <span className="ms-auto shrink-0 text-muted-foreground tabular-nums">
                        {student.sid}
                        {owners.length > 0 &&
                          ` · on ${owners.map((id) => `#${id}`).join(', ')}`}
                      </span>
                    </ComboboxItem>
                  );
                }}
              </ComboboxCollection>
            </ComboboxGroup>
          )}
        </ComboboxList>
        <div className="border-t p-1">
          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-start text-xs font-normal"
            onClick={onEnterName}
          >
            <PenLine aria-hidden="true" />
            Enter a name that isn't on the roster
          </Button>
        </div>
      </ComboboxContent>
    </Combobox>
  );
}

function NameForm({
  submissionId,
  initial,
  onSave,
  onCancel,
}: {
  submissionId: number;
  initial: Identity;
  onSave: (identity: Identity) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(initial.student_name);
  const [sid, setSid] = useState(initial.student_id);
  const nameId = `student-name-${submissionId}`;
  const sidId = `student-id-${submissionId}`;
  return (
    <form
      className="grid gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (name.trim())
          onSave({ student_name: name.trim(), student_id: sid.trim() });
      }}
    >
      <PopoverTitle className="font-medium">
        Student for submission {submissionId}
      </PopoverTitle>
      <div className="grid gap-1.5">
        <Label htmlFor={nameId}>Name</Label>
        <Input
          id={nameId}
          autoFocus
          autoComplete="off"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      </div>
      <div className="grid gap-1.5">
        <Label htmlFor={sidId}>Student ID</Label>
        <Input
          id={sidId}
          autoComplete="off"
          value={sid}
          onChange={(event) => setSid(event.target.value)}
        />
      </div>
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={!name.trim()}>
          Save
        </Button>
      </div>
    </form>
  );
}
