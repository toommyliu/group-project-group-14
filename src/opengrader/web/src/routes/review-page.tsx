import { useDeferredValue, useState } from 'react';
import { Link } from '@tanstack/react-router';
import { useQuery } from '@tanstack/react-query';
import { CircleCheck, ClipboardCheck, Download, SearchX } from 'lucide-react';
import { cn } from 'cn';
import {
  assignmentQAQuery,
  feedbackJobsQuery,
  submissionsQuery,
  type FeedbackJob,
  type Submission,
} from '@/api/queries';
import { rosterQuery, studentName, type Student } from '@/api/roster';
import { reviewRoute } from '@/router';
import { searchable } from '@/lib/search';
import {
  percentText,
  scoreText,
  versionColumns,
  type Column,
} from '@/lib/grade-columns';
import { PageHeading } from '@/components/page-heading';
import { ErrorAlert } from '@/components/error-alert';
import { FeedbackDownload } from '@/components/feedback-download';
import { FeedbackPdfButton } from '@/components/feedback-pdf-button';
import { FilterTabs } from '@/components/filter-tabs';
import { TableEmpty } from '@/components/table-empty';
import {
  FilterField,
  PeopleCell,
  PeopleHead,
  PeopleRow,
  PeopleTable,
  SortableHead,
} from '@/components/people-table';
import { Button } from '@/components/ui/button';
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from '@/components/ui/empty';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';

type SortKey = 'id' | 'student' | 'total';
type Filter = 'all' | 'graded' | 'progress' | 'missing';
type Row = {
  submission: Submission;
  own: Column[];
  scores: (number | null | undefined)[];
  complete: boolean;
  total: number;
  possible: number;
};

// The answer key parser rejects empty version names, so '' never names one.
const ALL = '';

const collator = new Intl.Collator(undefined, { sensitivity: 'base' });

// Spreadsheet apps run cells that start with these characters as formulas.
function csvCell(value: string) {
  const start = value.normalize('NFKC').trimStart();
  const safe =
    /^[\t\r]/.test(value) ||
    (/^[=+\-@|]/.test(start) && Number.isNaN(Number(value)))
      ? `'${value}`
      : value;
  return /[",\n\r]/.test(safe) ? `"${safe.replaceAll('"', '""')}"` : safe;
}

export default function ReviewPage() {
  const loaded = reviewRoute.useLoaderData();
  const { assignment } = loaded;
  const submissions = useQuery({
    ...submissionsQuery(assignment.id),
    initialData: loaded.submissions,
  });
  const qa = useQuery({
    ...assignmentQAQuery(assignment.id),
    initialData: loaded.qa,
  });
  const roster = useQuery({
    ...rosterQuery,
    initialData: loaded.students ?? undefined,
    enabled: loaded.students !== null,
  });
  const jobs = useQuery(feedbackJobsQuery);
  const feedback = new Map(
    jobs.data
      ?.filter((job) => job.assignment_id === assignment.id)
      .map((job) => [job.submission_id, job])
  );
  const [text, setText] = useState('');
  const search = useDeferredValue(searchable(text));
  const [filter, setFilter] = useState<Filter>('all');
  const [sort, setSort] = useState<{ by: SortKey; descending: boolean }>({
    by: 'id',
    descending: false,
  });
  const columnsByVersion = versionColumns(qa.data.document);
  const versions = [...columnsByVersion.keys()];
  const views = versions.length > 1 ? [ALL, ...versions] : versions;
  const [chosenView, setView] = useState(views[0]);
  const view = views.includes(chosenView) ? chosenView : views[0];
  const combined = versions.length > 1 && view === ALL;
  const columns = combined ? [] : (columnsByVersion.get(view) ?? []);

  const rows: Row[] = submissions.data.map((submission) => {
    const own = columnsByVersion.get(submission.version ?? '') ?? [];
    const scores = own.map((column) => submission.question_scores[column.id]);
    const graded = scores.filter(
      (score) => score !== null && score !== undefined
    );
    return {
      submission,
      own,
      scores,
      complete: graded.length === own.length && own.length > 0,
      total: graded.reduce<number>((sum, score) => sum + (score ?? 0), 0),
      possible: own.reduce((sum, column) => sum + column.points, 0),
    };
  });
  const versionRows = rows.filter(
    (row) =>
      combined || versions.length < 2 || (row.submission.version ?? '') === view
  );
  const sids = new Set(rows.map((row) => row.submission.student_id));
  const missing = (roster.data ?? [])
    .filter((student) => !sids.has(student.sid))
    .toSorted((a, b) => collator.compare(studentName(a), studentName(b)));
  const showMissing = combined || versions.length < 2;
  const complete = versionRows.filter((row) => row.complete);
  const average = complete.length
    ? complete.reduce((sum, row) => sum + row.total / (row.possible || 1), 0) /
      complete.length
    : null;

  const found = (...fields: string[]) =>
    !search || fields.some((field) => searchable(field).includes(search));
  const shownRows =
    filter === 'missing'
      ? []
      : versionRows
          .filter(
            (row) =>
              (filter === 'all' ||
                (filter === 'graded' ? row.complete : !row.complete)) &&
              found(
                row.submission.student_name,
                row.submission.student_id,
                `#${row.submission.id}`
              )
          )
          .toSorted((a, b) => {
            const order =
              sort.by === 'student'
                ? collator.compare(
                    a.submission.student_name,
                    b.submission.student_name
                  )
                : sort.by === 'total'
                  ? a.total / (a.possible || 1) - b.total / (b.possible || 1)
                  : a.submission.id - b.submission.id;
            return sort.descending ? -order : order;
          });
  const shownMissing =
    filter !== 'missing'
      ? []
      : sort.by === 'student' && sort.descending
        ? missing
            .filter((student) => found(studentName(student), student.sid))
            .toReversed()
        : missing.filter((student) => found(studentName(student), student.sid));

  function sortBy(by: SortKey) {
    setSort((current) => ({
      by,
      descending: current.by === by ? !current.descending : by === 'total',
    }));
  }

  function sorted(by: SortKey) {
    if (sort.by !== by) return false;
    return sort.descending ? 'desc' : 'asc';
  }

  function download() {
    const header = [
      'Submission',
      'Student name',
      'Student ID',
      'Version',
      ...columns.map((column) => `Q${column.label} (${column.points})`),
      'Total',
      'Possible',
      'Percent',
      'Status',
    ];
    const lines = [
      header,
      ...versionRows.map((row) => [
        String(row.submission.id),
        row.submission.student_name,
        row.submission.student_id,
        row.submission.version ?? '',
        ...columns.map((_, index) => scoreText(row.scores[index])),
        scoreText(row.total),
        String(row.possible),
        percentText(row.total, row.possible),
        row.complete ? 'Graded' : 'In progress',
      ]),
      ...(showMissing
        ? missing.map((student) => [
            '',
            studentName(student),
            student.sid,
            '',
            ...columns.map(() => ''),
            '',
            '',
            '',
            'No submission',
          ])
        : []),
    ];
    const blob = new Blob(
      [lines.map((line) => line.map(csvCell).join(',')).join('\n') + '\n'],
      { type: 'text/csv' }
    );
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `${assignment.title || 'grades'}${versions.length > 1 && !combined ? ` (Version ${view})` : ''}.csv`;
    link.click();
    URL.revokeObjectURL(link.href);
  }

  const template = [
    'auto',
    'minmax(12rem,1fr)',
    ...(combined ? ['auto'] : []),
    ...columns.map(() => 'minmax(3.5rem,auto)'),
    'auto',
    'auto',
  ].join(' ');

  return (
    <>
      <PageHeading
        title="Review grades"
        description={
          versionRows.length
            ? `${complete.length} of ${versionRows.length} fully graded${average === null ? '' : ` · average ${Math.round(average * 1000) / 10}%`}`
            : undefined
        }
        actions={
          rows.length > 0 && (
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" onClick={download}>
                <Download aria-hidden="true" />
                {versions.length > 1 && !combined
                  ? `Download version ${view} CSV`
                  : 'Download CSV'}
              </Button>
              <FeedbackDownload
                assignmentId={assignment.id}
                filename={`${assignment.title || 'Assignment'}${versions.length > 1 && !combined ? ` (Version ${view})` : ''} feedback.zip`}
                label={
                  versions.length > 1 && !combined
                    ? `Download version ${view} feedback PDFs`
                    : 'Download feedback PDFs'
                }
                rows={versionRows.map((row) => ({
                  submissionId: row.submission.id,
                  name:
                    row.submission.student_name ||
                    `Submission ${row.submission.id}`,
                  complete: row.complete,
                  job: feedback.get(row.submission.id),
                }))}
              />
            </div>
          )
        }
      />
      {(submissions.isError || qa.isError) && (
        <ErrorAlert
          className="mb-4"
          title="Could not refresh grades. Showing the last loaded scores."
          error={submissions.error ?? qa.error}
        />
      )}
      {roster.isError && (
        <ErrorAlert
          className="mb-4"
          title="Could not refresh the roster. Students without a submission may be out of date."
          error={roster.error}
        />
      )}
      {rows.length === 0 ? (
        <Empty className="border py-16">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <ClipboardCheck />
            </EmptyMedia>
            <EmptyTitle>Nothing to review yet</EmptyTitle>
            <EmptyDescription>
              Grades show up here once scans are split into submissions.
            </EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            <Button
              render={
                <Link
                  to="/assignments/$assignmentId/submissions"
                  params={{ assignmentId: assignment.id }}
                />
              }
            >
              Go to submissions
            </Button>
          </EmptyContent>
        </Empty>
      ) : (
        <section
          aria-label="Grades"
          className="@container flex min-h-0 flex-1 flex-col gap-3"
        >
          <div className="flex flex-wrap items-center gap-2">
            <FilterTabs
              label="Show"
              value={filter}
              onChange={setFilter}
              options={[
                { value: 'all', label: 'All', count: versionRows.length },
                {
                  value: 'graded',
                  label: 'Fully graded',
                  count: complete.length,
                },
                {
                  value: 'progress',
                  label: 'In progress',
                  count: versionRows.length - complete.length,
                },
                ...(showMissing && roster.data?.length
                  ? [
                      {
                        value: 'missing' as const,
                        label: 'No submission',
                        count: missing.length,
                      },
                    ]
                  : []),
              ]}
            />
            <div className="flex min-w-0 flex-1 basis-[22rem] items-center justify-end gap-2">
              {versions.length > 1 && (
                <Select
                  value={view}
                  onValueChange={(value) => {
                    if (value === null) return;
                    setView(value);
                    if (value !== ALL && filter === 'missing') setFilter('all');
                  }}
                >
                  <SelectTrigger aria-label="Version" className="w-36 text-sm">
                    <SelectValue>
                      {view === ALL ? 'All versions' : `Version ${view}`}
                    </SelectValue>
                  </SelectTrigger>
                  <SelectContent align="start">
                    {views.map((item) => (
                      <SelectItem key={item} value={item}>
                        {item === ALL ? 'All versions' : `Version ${item}`}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
              <FilterField
                label="Filter by name or SID"
                value={text}
                onChange={setText}
                className="min-w-0 flex-1 sm:w-auto @4xl:w-64 @4xl:flex-none"
              />
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-x-auto">
            <PeopleTable
              aria-label="Grades"
              columns="h-full w-max min-w-full"
              style={{ gridTemplateColumns: template }}
              header={
                <>
                  <SortableHead
                    label="#"
                    sorted={sorted('id')}
                    onSort={() => sortBy('id')}
                  />
                  <SortableHead
                    label="Student"
                    sorted={sorted('student')}
                    onSort={() => sortBy('student')}
                  />
                  {combined && <PeopleHead>Version</PeopleHead>}
                  {columns.map((column) => (
                    <PeopleHead
                      key={column.id}
                      className="justify-end"
                      title={`${column.points} points`}
                    >
                      Q{column.label}
                    </PeopleHead>
                  ))}
                  <SortableHead
                    label="Total"
                    className="justify-end"
                    sorted={sorted('total')}
                    onSort={() => sortBy('total')}
                  />
                  <PeopleHead>
                    <span className="sr-only">Feedback PDF</span>
                  </PeopleHead>
                </>
              }
            >
              {shownRows.map((row) => (
                <ReviewRow
                  key={row.submission.id}
                  assignmentId={assignment.id}
                  row={row}
                  feedback={feedback.get(row.submission.id)}
                  own={combined ? [] : row.own}
                  showVersion={combined}
                />
              ))}
              {shownMissing.map((student) => (
                <MissingRow
                  key={student.id}
                  student={student}
                  columns={columns.length}
                  showVersion={combined}
                />
              ))}
              {shownRows.length === 0 &&
                shownMissing.length === 0 &&
                (search ? (
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
                        : filter === 'graded'
                          ? 'No submission is fully graded yet'
                          : 'Every submission is fully graded'
                    }
                    action={
                      <Button
                        variant="outline"
                        onClick={() => setFilter('all')}
                      >
                        Show all submissions
                      </Button>
                    }
                  />
                ))}
            </PeopleTable>
          </div>
        </section>
      )}
    </>
  );
}

function ReviewRow({
  assignmentId,
  row: { submission, scores, complete, total, possible },
  feedback,
  own,
  showVersion,
}: {
  assignmentId: string;
  row: Row;
  feedback: FeedbackJob | undefined;
  own: Column[];
  showVersion: boolean;
}) {
  return (
    <PeopleRow>
      <PeopleCell className="tabular-nums">
        <Link
          to="/assignments/$assignmentId/grade"
          params={{ assignmentId }}
          search={{ submission: submission.id }}
          className="underline-offset-4 hover:underline"
        >
          {submission.id}
        </Link>
      </PeopleCell>
      <PeopleCell className="grid py-1.5">
        <Link
          to="/assignments/$assignmentId/review/$submissionId"
          params={{ assignmentId, submissionId: String(submission.id) }}
          className={cn(
            'truncate underline-offset-4 hover:underline',
            !submission.student_name && 'text-muted-foreground'
          )}
        >
          {submission.student_name || 'No student'}
        </Link>
        {submission.student_id && (
          <span className="text-xs text-muted-foreground tabular-nums">
            {submission.student_id}
          </span>
        )}
      </PeopleCell>
      {showVersion && <PeopleCell>{submission.version}</PeopleCell>}
      {own.map((column, index) => (
        <PeopleCell key={column.id} className="text-end tabular-nums">
          {scores[index] === null || scores[index] === undefined ? (
            <Link
              to="/assignments/$assignmentId/grade"
              params={{ assignmentId }}
              search={{ submission: submission.id, question: column.id }}
              className="text-muted-foreground underline-offset-4 hover:underline"
              aria-label={`Grade question ${column.label} of submission ${submission.id}`}
            >
              –
            </Link>
          ) : (
            scoreText(scores[index])
          )}
        </PeopleCell>
      ))}
      <PeopleCell className="py-1.5 text-end whitespace-nowrap tabular-nums">
        <span className="font-medium">{scoreText(total)}</span>
        <span className="text-muted-foreground"> / {possible}</span>
        <span className="block text-xs text-muted-foreground">
          {complete ? percentText(total, possible) : 'In progress'}
        </span>
      </PeopleCell>
      <PeopleCell className="py-0">
        <FeedbackPdfButton
          assignmentId={assignmentId}
          submissionId={submission.id}
          job={feedback}
          graded={scores.some((score) => score !== null && score !== undefined)}
        />
      </PeopleCell>
    </PeopleRow>
  );
}

function MissingRow({
  student,
  columns,
  showVersion,
}: {
  student: Student;
  columns: number;
  showVersion: boolean;
}) {
  return (
    <PeopleRow>
      <PeopleCell />
      <PeopleCell className="grid py-1.5">
        <span className="truncate">{studentName(student)}</span>
        <span className="text-xs text-muted-foreground tabular-nums">
          {student.sid}
        </span>
      </PeopleCell>
      {showVersion && <PeopleCell />}
      {Array.from({ length: columns }, (_, index) => (
        <PeopleCell key={index} />
      ))}
      <PeopleCell className="text-end text-muted-foreground">
        No submission
      </PeopleCell>
      <PeopleCell />
    </PeopleRow>
  );
}
