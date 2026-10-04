import { ApiError } from '@/api/client';
import { errorMessage } from '@/lib/error-message';
import { PageHeading } from './page-heading';
import { Button, buttonVariants } from './ui/button';

export function StartupError({ error }: { error: unknown }) {
  const missing = error instanceof ApiError && error.status === 404;
  return (
    <main className="mx-auto w-full max-w-[90rem] px-4 pt-8 pb-16 text-sm leading-normal antialiased sm:px-8 lg:px-14">
      <div className="mx-auto flex min-h-[calc(100svh-6rem)] w-full max-w-6xl flex-col items-start justify-center">
        <PageHeading
          className="mb-6 w-full"
          title={missing ? 'Course not found' : 'Couldn’t open this course'}
          description={
            missing
              ? 'It may have been removed, or the link may be out of date.'
              : errorMessage(
                  error,
                  'Check that Opengrader is still running, then try again.'
                )
          }
        />
        {missing ? (
          <a href="/courses" className={buttonVariants({ size: 'lg' })}>
            Back to courses
          </a>
        ) : (
          <div className="flex flex-wrap items-center gap-4">
            <Button size="lg" onClick={() => window.location.reload()}>
              Try again
            </Button>
            <a
              href="/courses"
              className="inline-flex min-h-11 items-center underline underline-offset-4"
            >
              Back to courses
            </a>
          </div>
        )}
      </div>
    </main>
  );
}
