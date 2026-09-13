import { LoaderCircle } from 'lucide-react';
import { PageHeading } from './page-heading';
import { Skeleton } from './ui/skeleton';

// Loading skeletons.

export function AssignmentsLoading() {
  return (
    <div aria-busy="true" aria-label="Loading assignments">
      <PageHeading
        title="Assignments"
        description="Choose an assignment to review its responses."
      />
      <div className="border-t" aria-hidden="true">
        {[0, 1, 2].map((item) => (
          <div
            key={item}
            className="flex min-h-25 flex-col justify-center gap-2 border-b py-6"
          >
            <Skeleton className="h-6 w-56 max-w-full" />
            <Skeleton className="h-5 w-32" />
          </div>
        ))}
      </div>
    </div>
  );
}

export function AssignmentLoading() {
  return (
    <div aria-busy="true" aria-label="Loading assignment">
      <PageHeading
        title={<Skeleton className="h-9 w-72 max-w-full" />}
        description="Review student responses and existing grades."
      />
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
