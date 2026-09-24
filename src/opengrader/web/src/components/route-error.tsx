import { errorMessage } from '@/lib/error-message';
import {
  Link,
  useRouter,
  type ErrorComponentProps,
} from '@tanstack/react-router';
import { PageHeading } from './page-heading';
import { Button } from './ui/button';

export function RouteError({ error }: ErrorComponentProps) {
  const router = useRouter();
  return (
    <>
      <PageHeading
        title="Unable to load this page"
        description={errorMessage(error)}
      />
      <div
        className="flex min-h-80 items-start gap-4 border-t py-6"
        role="alert"
      >
        <Button variant="outline" onClick={() => void router.invalidate()}>
          Try again
        </Button>
        <Link
          to="/assignments"
          className="inline-flex min-h-11 items-center underline underline-offset-4"
        >
          All assignments
        </Link>
      </div>
    </>
  );
}

export function RouteNotFound() {
  return (
    <>
      <PageHeading
        title="Page not found"
        description="This assignment or submission is no longer available."
      />
      <Link
        to="/assignments"
        className="inline-flex min-h-11 items-center underline underline-offset-4"
      >
        All assignments
      </Link>
    </>
  );
}
