import { LoaderCircle } from 'lucide-react';
import { PageHeading } from './page-heading';
import { Skeleton } from './ui/skeleton';

export function CoursesLoading() {
  return (
    <div aria-busy="true" aria-label="Loading courses">
      <PageHeading
        className="mb-6"
        title="Courses"
        description="Open a course to manage its assignments and grading."
      />
      <div className="@container" aria-hidden="true">
        <div className="grid grid-cols-1 gap-4 @xl:grid-cols-2 @5xl:grid-cols-3">
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
    </div>
  );
}

export function AssignmentsLoading() {
  return (
    <div aria-busy="true" aria-label="Loading assignments">
      <PageHeading className="mb-6" title="Assignments" />
      <div className="@container" aria-hidden="true">
        <div className="mb-8 grid grid-cols-2 gap-x-6 gap-y-5 border-y py-5 @lg:grid-cols-3">
          {[0, 1, 2].map((item) => (
            <div key={item} className="space-y-2">
              <Skeleton className="h-5 w-24" />
              <Skeleton className="h-8 w-16" />
              <Skeleton className="h-5 w-32 max-w-full" />
            </div>
          ))}
        </div>
        <div className="grid grid-cols-1 gap-4 @xl:grid-cols-2 @5xl:grid-cols-3">
          {[0, 1, 2].map((item) => (
            <div key={item} className="space-y-5 rounded-lg border p-5">
              <div className="space-y-2">
                <Skeleton className="h-6 w-56 max-w-full" />
                <Skeleton className="h-5 w-40 max-w-full" />
              </div>
              <Skeleton className="h-9 w-full" />
              <Skeleton className="h-11 w-full" />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export function AssignmentLoading() {
  return (
    <div aria-busy="true" aria-label="Loading assignment">
      <PageHeading title={<Skeleton className="h-9 w-40 max-w-full" />} />
      <div
        className="flex flex-wrap items-end gap-6 border-b pb-6"
        aria-hidden="true"
      >
        <div className="grid gap-2">
          <span>Submission</span>
          <Skeleton className="h-11 w-52" />
        </div>
        <div className="grid gap-2">
          <span>Question</span>
          <Skeleton className="h-11 w-44" />
        </div>
        <div className="ms-auto flex gap-3">
          <Skeleton className="h-11 w-24" />
          <Skeleton className="h-11 w-24" />
        </div>
      </div>
      <div
        className="grid min-h-[32rem] gap-8 pt-6 min-[60rem]:grid-cols-[minmax(0,1fr)_minmax(18rem,23rem)] min-[75rem]:gap-16"
        aria-hidden="true"
      >
        <div className="space-y-4">
          <Skeleton className="h-6 w-24" />
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-96 w-full" />
        </div>
        <div className="space-y-3">
          <Skeleton className="mb-4 h-6 w-24" />
          {[0, 1, 2].map((item) => (
            <Skeleton key={item} className="h-16 w-full" />
          ))}
        </div>
      </div>
    </div>
  );
}

export function GradeLoading() {
  return (
    <div
      className="flex min-h-0 flex-1 flex-col"
      aria-busy="true"
      aria-label="Loading grading"
    >
      <PageHeading className="mb-4" title="Grade" />
      <div
        className="grid gap-8 border-t pt-4 lg:min-h-0 lg:flex-1 lg:grid-cols-[minmax(0,1fr)_minmax(20rem,26rem)] lg:grid-rows-[minmax(0,1fr)] lg:gap-0"
        aria-hidden="true"
      >
        <div className="space-y-4 lg:pe-6">
          <Skeleton className="h-11 w-full" />
          <Skeleton className="h-96 w-full" />
        </div>
        <div className="space-y-3 lg:border-s lg:ps-6">
          <Skeleton className="h-11 w-full" />
          <Skeleton className="mb-6 h-8 w-full" />
          {[0, 1, 2].map((item) => (
            <Skeleton key={item} className="h-16 w-full" />
          ))}
        </div>
      </div>
      <div
        className="flex items-center gap-3 border-t py-3 lg:pb-0"
        aria-hidden="true"
      >
        <Skeleton className="h-11 w-60" />
        <Skeleton className="ms-auto h-11 w-72" />
      </div>
    </div>
  );
}

export function PeopleLoading() {
  return (
    <div aria-busy="true" aria-label="Loading people">
      <PageHeading title="People" />
      <div className="space-y-3" aria-hidden="true">
        <Skeleton className="h-11 w-72 max-w-full" />
        {[0, 1, 2, 3, 4].map((item) => (
          <Skeleton key={item} className="h-11 w-full" />
        ))}
      </div>
    </div>
  );
}

/** Reserve the icon's space so loading does not shift nearby controls. */
export function LoadingIndicator({
  loading,
  label,
}: {
  loading: boolean;
  label: string;
}) {
  return (
    <span className="inline-flex size-4 shrink-0" role="status">
      {loading && (
        <>
          <LoaderCircle
            className="size-4 motion-safe:animate-spin"
            aria-hidden="true"
          />
          <span className="sr-only">{label}</span>
        </>
      )}
    </span>
  );
}

export function PaneLoading({ label }: { label: string }) {
  return (
    <p
      role="status"
      className="flex min-h-48 flex-1 items-center justify-center gap-2 text-muted-foreground"
    >
      <LoaderCircle
        className="size-4 motion-safe:animate-spin"
        aria-hidden="true"
      />
      {label}
    </p>
  );
}
