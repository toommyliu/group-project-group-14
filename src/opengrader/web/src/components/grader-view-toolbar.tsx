import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useRouter } from '@tanstack/react-router';
import { Eye } from 'lucide-react';
import { authQuery, viewAs } from '@/api/auth';
import { errorMessage } from '@/lib/error-message';
import { Button } from '@/components/ui/button';

export function GraderViewToolbar() {
  const queryClient = useQueryClient();
  const router = useRouter();
  const { data } = useQuery(authQuery);
  const [switching, setSwitching] = useState(false);
  const [error, setError] = useState('');

  if (!data?.viewing_as_grader) return null;

  async function returnToInstructorView() {
    setSwitching(true);
    setError('');
    try {
      await viewAs('instructor');
      await queryClient.invalidateQueries({ queryKey: authQuery.queryKey });
      await router.invalidate();
    } catch (cause) {
      setError(errorMessage(cause, 'Could not switch views. Try again.'));
    } finally {
      setSwitching(false);
    }
  }

  return (
    <div className="pointer-events-none sticky top-[max(1rem,env(safe-area-inset-top))] z-40 mb-4 flex shrink-0 justify-center">
      <section
        aria-label="Grader view"
        className="pointer-events-auto flex max-w-full items-center gap-3 bg-popover py-1.5 ps-3 pe-1.5 text-sm text-popover-foreground shadow-lg ring-1 ring-foreground/10"
      >
        {error ? (
          <p role="alert" className="min-w-0 text-destructive">
            {error}
          </p>
        ) : (
          <p className="flex min-w-0 items-center gap-2">
            <Eye className="size-4 shrink-0" aria-hidden="true" />
            <span className="truncate">Viewing as a grader</span>
          </p>
        )}
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={switching}
          onClick={() => void returnToInstructorView()}
        >
          {switching ? 'Switching…' : 'Back to instructor view'}
        </Button>
      </section>
    </div>
  );
}
