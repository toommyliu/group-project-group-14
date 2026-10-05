import { useRef, useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { ArrowRight, LoaderCircle } from 'lucide-react';
import { cn } from 'cn';
import { authQuery } from '@/api/auth';
import {
  courseTerm,
  coursesQuery,
  createCourse,
  currentTerm,
  terms,
  type CourseSummary,
} from '@/api/workspace';
import { CourseAvatar } from '@/components/course-avatar';
import { CourseFields } from '@/components/course-fields';
import {
  courseDraftErrors,
  type CourseDraft,
  type CourseDraftErrors,
} from '@/lib/course-draft';
import { ErrorAlert } from '@/components/error-alert';
import { HowItWorks } from '@/components/how-it-works';
import { PageHeading } from '@/components/page-heading';
import { SteadyLabel } from '@/components/steady-label';
import { RemoveCourseButton } from '@/components/remove-course-button';
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import { FieldError } from '@/components/ui/field';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { coursesRoute } from '@/router';

const allTerms = 'all';

function newCourse(): CourseDraft {
  return { number: '', name: '', ...currentTerm() };
}

function byTerm(courses: CourseSummary[]) {
  const groups = new Map<string, CourseSummary[]>();
  for (const course of courses) {
    const label = courseTerm(course);
    groups.set(label, [...(groups.get(label) ?? []), course]);
  }
  const rank = ({ year, term }: CourseSummary) =>
    (year ?? 0) * 10 + (term ? terms.indexOf(term) + 1 : 0);
  return [...groups.entries()]
    .map(([label, members]) => ({
      label: label || 'No term',
      courses: members,
    }))
    .sort((a, b) => rank(b.courses[0]) - rank(a.courses[0]));
}

export default function WorkspaceList() {
  const workspaces = useQuery(coursesQuery);
  const auth = useQuery(authQuery);
  const canCreate =
    auth.data && (!auth.data.enabled || auth.data.role === 'instructor');
  const [draft, setDraft] = useState(newCourse);
  const [errors, setErrors] = useState<CourseDraftErrors>({});
  const numberInput = useRef<HTMLInputElement>(null);
  const nameInput = useRef<HTMLInputElement>(null);
  const create = useMutation({
    mutationFn: () =>
      createCourse({
        ...draft,
        number: draft.number.trim(),
        name: draft.name.trim(),
      }),
    onSuccess: (workspace) => window.location.assign(workspace.url),
    onError: () => numberInput.current?.focus(),
  });
  function submit(event: FormEvent) {
    event.preventDefault();
    if (create.isPending) return;
    const next = courseDraftErrors(draft);
    setErrors(next);
    if (next.number) return numberInput.current?.focus();
    if (next.name) return nameInput.current?.focus();
    create.mutate();
  }
  const courses = workspaces.data;
  const { term } = coursesRoute.useSearch();
  const navigate = useNavigate();
  const groups = courses ? byTerm(courses) : [];
  const shown = term ? groups.filter((group) => group.label === term) : groups;
  const termItems = [
    { value: allTerms, label: 'All terms' },
    ...groups.map((group) => ({ value: group.label, label: group.label })),
  ];
  const filterTerm = (next: string | null) =>
    void navigate({
      to: '/courses',
      search: { term: next && next !== allTerms ? next : undefined },
    });

  return (
    <AlertDialog
      onOpenChange={(open, details) => {
        if (create.isPending) return details.cancel();
        if (open) {
          setDraft(newCourse());
          setErrors({});
          create.reset();
        }
      }}
    >
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <PageHeading
          className="mb-0 flex-1"
          title="Courses"
          description="Open a course to manage its assignments and grading."
        />
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {(groups.length > 1 || term) && (
            <Select
              items={termItems}
              value={term ?? allTerms}
              onValueChange={filterTerm}
            >
              <SelectTrigger
                aria-label="Filter by term"
                className="min-h-11 min-w-36 px-3 text-sm data-[size=default]:h-auto"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent align="end">
                {termItems.map((item) => (
                  <SelectItem key={item.label} value={item.value}>
                    {item.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          {canCreate && (
            <AlertDialogTrigger render={<Button className="shrink-0" />}>
              Create course
            </AlertDialogTrigger>
          )}
        </div>
      </div>
      {workspaces.isPending && <CoursesLoading />}
      {workspaces.isError && (
        <ErrorAlert
          className="mb-6"
          title="Courses could not be loaded."
          error={workspaces.error}
        >
          <Button variant="outline" onClick={() => void workspaces.refetch()}>
            Retry
          </Button>
        </ErrorAlert>
      )}
      {courses && courses.length > 0 && term && shown.length === 0 && (
        <div className="space-y-4 border-t py-8">
          <p>No courses in {term}.</p>
          <Button variant="outline" onClick={() => filterTerm(null)}>
            Show all terms
          </Button>
        </div>
      )}
      {shown.length > 0 && (
        <div className="@container space-y-8">
          {shown.map((group) => (
            <section key={group.label} aria-label={group.label}>
              <h2 className="mb-3 text-sm font-medium text-muted-foreground">
                {group.label}
              </h2>
              <ul className="grid grid-cols-1 gap-4 @xl:grid-cols-2 @5xl:grid-cols-3">
                {group.courses.map((workspace) => (
                  <li key={workspace.id}>
                    <CourseCard course={workspace} canRemove={canCreate} />
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
      {courses?.length === 0 && (
        <div className="min-h-80 space-y-4 border-t py-8">
          <h2 className="text-base font-semibold">No courses yet</h2>
          <p>
            {canCreate
              ? 'Create a course to start adding assignments.'
              : 'Ask an instructor to add you to a course.'}
          </p>
          {canCreate && <HowItWorks className="pt-4" />}
        </div>
      )}
      {canCreate && (
        <AlertDialogContent initialFocus={numberInput}>
          <form noValidate onSubmit={submit} className="grid gap-4">
            <AlertDialogHeader>
              <AlertDialogTitle>Create a course</AlertDialogTitle>
            </AlertDialogHeader>
            <CourseFields
              draft={draft}
              onChange={(next) => {
                setDraft(next);
                setErrors({});
                if (create.isError) create.reset();
              }}
              errors={errors}
              readOnly={create.isPending}
              numberInput={numberInput}
              nameInput={nameInput}
            />
            <FieldError>{create.error?.message}</FieldError>
            <AlertDialogFooter>
              <AlertDialogCancel disabled={create.isPending}>
                Cancel
              </AlertDialogCancel>
              <Button type="submit" disabled={create.isPending}>
                <SteadyLabel
                  busy={create.isPending}
                  idle="Create course"
                  working={
                    <>
                      <LoaderCircle
                        aria-hidden="true"
                        className="motion-safe:animate-spin"
                      />
                      Creating course…
                    </>
                  }
                />
              </Button>
            </AlertDialogFooter>
          </form>
        </AlertDialogContent>
      )}
    </AlertDialog>
  );
}

function CourseCard({
  course,
  canRemove,
}: {
  course: CourseSummary;
  canRemove: boolean | undefined;
}) {
  const queryClient = useQueryClient();
  return (
    <article
      className={cn(
        'group relative flex h-full items-start gap-4 rounded-lg border p-5',
        course.available &&
          'has-[a:focus-visible]:outline-2 has-[a:focus-visible]:outline-offset-2 has-[a:focus-visible]:outline-foreground has-[a:hover]:bg-muted/40 motion-safe:transition-colors motion-safe:duration-150'
      )}
    >
      <CourseAvatar
        id={course.id}
        name={course.number}
        muted={!course.available}
      />
      <div className="min-w-0 flex-1">
        <h3 className="truncate text-base font-medium" title={course.number}>
          {course.available ? (
            <a
              href={course.url}
              className="outline-none after:absolute after:inset-0"
            >
              {course.number}
            </a>
          ) : (
            course.number
          )}
        </h3>
        {course.name && (
          <p className="truncate text-sm" title={course.name}>
            {course.name}
          </p>
        )}
        {!course.available && (
          <p className="mt-1 text-sm text-muted-foreground">
            Folder unavailable. Ask the person who set up Opengrader to
            reconnect it.
          </p>
        )}
        {!course.available && canRemove && (
          <RemoveCourseButton
            size="sm"
            className="mt-3"
            course={course}
            onRemoved={() =>
              queryClient.invalidateQueries({ queryKey: coursesQuery.queryKey })
            }
          />
        )}
      </div>
      {course.available && (
        <ArrowRight
          className="mt-0.5 size-4 shrink-0 text-muted-foreground group-has-[a:hover]:text-foreground"
          aria-hidden="true"
        />
      )}
    </article>
  );
}

function CoursesLoading() {
  return (
    <div className="@container" aria-busy="true" aria-label="Loading courses">
      <div
        className="grid grid-cols-1 gap-4 @xl:grid-cols-2 @5xl:grid-cols-3"
        aria-hidden="true"
      >
        {[0, 1, 2].map((item) => (
          <div key={item} className="flex gap-4 rounded-lg border p-5">
            <Skeleton className="size-10 shrink-0" />
            <div className="flex-1 space-y-2">
              <Skeleton className="h-6 w-48 max-w-full" />
              <Skeleton className="h-5 w-32 max-w-full" />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
