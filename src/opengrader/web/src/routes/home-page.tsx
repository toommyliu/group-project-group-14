import { Link } from '@tanstack/react-router';
import { useQuery } from '@tanstack/react-query';
import { authQuery } from '@/api/auth';
import { HowItWorks } from '@/components/how-it-works';
import { ModeToggle } from '@/components/mode-toggle';
import { PageHeading } from '@/components/page-heading';
import { buttonVariants } from '@/components/ui/button';

export default function HomePage() {
  const auth = useQuery(authQuery).data;
  return (
    <div className="mx-auto flex min-h-[calc(100svh-6rem)] w-full max-w-6xl flex-col items-start justify-center">
      <PageHeading
        className="mb-6 w-full"
        title="Opengrader"
        description="Grade paper assignments from a stack of scans, with no friction."
        actions={<ModeToggle />}
      />
      {auth?.enabled && !auth.role ? (
        <a href="/auth/login" className={buttonVariants({ size: 'lg' })}>
          Sign in with Google
        </a>
      ) : (
        <Link to="/courses" className={buttonVariants({ size: 'lg' })}>
          Start grading
        </Link>
      )}
      <HowItWorks className="mt-12 border-t pt-8" />
    </div>
  );
}
