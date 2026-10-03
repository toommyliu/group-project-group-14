import { useRef, useState, type FormEvent } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { ArrowRight, LoaderCircle } from 'lucide-react';
import { cn } from 'cn';
import { authQuery } from '@/api/auth';
import { readData, serverClient } from '@/api/client';
import { coursesQuery, type CourseSummary } from '@/api/workspace';
import { CourseAvatar } from '@/components/course-avatar';
import { ErrorAlert } from '@/components/error-alert';
import { HowItWorks } from '@/components/how-it-works';
import { PageHeading } from '@/components/page-heading';
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
import { Field, FieldError, FieldLabel } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';

const openedAt = new Intl.DateTimeFormat(undefined, {
  month: 'short',
  day: 'numeric',
  year: 'numeric',
});

export default function WorkspaceList() {
  const workspaces = useQuery(coursesQuery);
  const auth = useQuery(authQuery);
  const canCreate =
    auth.data && (!auth.data.enabled || auth.data.role === 'instructor');
  const [name, setName] = useState('');
  const [nameError, setNameError] = useState('');
  const nameInput = useRef<HTMLInputElement>(null);
  const create = useMutation({
    mutationFn: async () =>
      readData(await serverClient.POST('/api/workspaces', { body: { name } })),
    onSuccess: (workspace) => window.location.assign(workspace.url),
    onError: () => nameInput.current?.focus(),
  });
  function submit(event: FormEvent) {
    event.preventDefault();
    if (create.isPending) return;
    if (!name.trim()) {
      setNameError('Enter a course name.');
      nameInput.current?.focus();
      return;
    }
    setNameError('');
    create.mutate();
  }
  const error = nameError || create.error?.message;
  const courses = workspaces.data;

  return (
    <AlertDialog
      onOpenChange={(_, details) => {
        if (create.isPending) details.cancel();
      }}
    >
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <PageHeading
          className="mb-0 flex-1"
          title="Courses"
          description="Open a course to manage its assignments and grading."
        />
        {canCreate && (
          <AlertDialogTrigger
            render={<Button className="shrink-0 self-start sm:self-auto" />}
          >
            Create course
          </AlertDialogTrigger>
        )}
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
      {courses && courses.length > 0 && (
        <div className="@container">
          <ul className="grid grid-cols-1 gap-4 @xl:grid-cols-2 @5xl:grid-cols-3">
            {courses.map((workspace) => (
              <li key={workspace.id}>
                <CourseCard course={workspace} />
              </li>
            ))}
          </ul>
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
        <AlertDialogContent initialFocus={nameInput}>
          <form noValidate onSubmit={submit} className="grid gap-4">
            <AlertDialogHeader>
              <AlertDialogTitle>Create a course</AlertDialogTitle>
            </AlertDialogHeader>
            <Field data-invalid={Boolean(error)}>
              <FieldLabel htmlFor="workspace-name">Course name</FieldLabel>
              <Input
                ref={nameInput}
                id="workspace-name"
                name="workspace-name"
                value={name}
                onChange={(event) => {
                  setName(event.target.value);
                  setNameError('');
                  if (create.isError) create.reset();
                }}
                aria-required="true"
                readOnly={create.isPending}
                maxLength={120}
                placeholder="CS101 Fall 2026"
                aria-invalid={Boolean(error) || undefined}
                aria-describedby={error ? 'workspace-error' : undefined}
              />
              <FieldError id="workspace-error">{error}</FieldError>
            </Field>
            <AlertDialogFooter>
              <AlertDialogCancel disabled={create.isPending}>
                Cancel
              </AlertDialogCancel>
              <Button type="submit" disabled={create.isPending}>
                {create.isPending && (
                  <LoaderCircle
                    aria-hidden="true"
                    className="motion-safe:animate-spin"
                  />
                )}
                {create.isPending ? 'Creating course…' : 'Create course'}
              </Button>
            </AlertDialogFooter>
          </form>
        </AlertDialogContent>
      )}
    </AlertDialog>
  );
}

function CourseCard({ course }: { course: CourseSummary }) {
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
        name={course.name}
        muted={!course.available}
      />
      <div className="min-w-0 flex-1">
        <h2 className="truncate text-base font-medium" title={course.name}>
          {course.available ? (
            <a
              href={course.url}
              className="outline-none after:absolute after:inset-0"
            >
              {course.name}
            </a>
          ) : (
            course.name
          )}
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          {!course.available
            ? 'Folder unavailable. Ask the person who set up Opengrader to reconnect it.'
            : course.last_opened
              ? `Opened ${openedAt.format(new Date(course.last_opened))}`
              : 'Not opened yet'}
        </p>
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
