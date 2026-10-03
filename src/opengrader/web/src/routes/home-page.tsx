import { Link } from '@tanstack/react-router';
import { AuthControl } from '@/components/auth-control';
import { HowItWorks } from '@/components/how-it-works';
import { ModeToggle } from '@/components/mode-toggle';
import { PageHeading } from '@/components/page-heading';
import { buttonVariants } from '@/components/ui/button';

export default function HomePage() {
  return (
    // Center the page within the viewport below the content padding (6rem).
    <div className="mx-auto flex min-h-[calc(100svh-6rem)] w-full max-w-6xl flex-col items-start justify-center">
      <PageHeading
        className="mb-6 w-full"
        title="Opengrader"
        description="Grade paper assignments from a stack of scans, with no friction."
        actions={
          <div className="flex items-center gap-2">
            <AuthControl />
            <ModeToggle />
          </div>
        }
      />
      <Link to="/assignments" className={buttonVariants({ size: 'lg' })}>
        Start grading
      </Link>
      <HowItWorks className="mt-12 border-t pt-8" />
    </div>
  );
}
