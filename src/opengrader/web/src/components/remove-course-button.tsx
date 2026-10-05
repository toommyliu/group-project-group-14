import { useMutation } from '@tanstack/react-query';
import { removeCourse } from '@/api/workspace';
import { ErrorAlert } from '@/components/error-alert';
import { SteadyLabel } from '@/components/steady-label';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';

export function RemoveCourseButton({
  course,
  onRemoved,
  size,
  className,
}: {
  course: { id: string; number: string };
  onRemoved: () => unknown;
  size?: 'sm';
  className?: string;
}) {
  const remove = useMutation({
    mutationFn: () => removeCourse(course.id),
    onSuccess: onRemoved,
  });
  return (
    <AlertDialog
      onOpenChange={(open, details) => {
        if (remove.isPending) return details.cancel();
        if (open) remove.reset();
      }}
    >
      <AlertDialogTrigger
        render={<Button variant="outline" size={size} className={className} />}
      >
        Remove course
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle className="wrap-anywhere">
            Remove {course.number}?
          </AlertDialogTitle>
          <AlertDialogDescription>
            Graders will lose access. Its files and grades won't be deleted.
          </AlertDialogDescription>
        </AlertDialogHeader>
        {remove.isError && <ErrorAlert error={remove.error} />}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={remove.isPending}>
            Cancel
          </AlertDialogCancel>
          <AlertDialogAction
            type="button"
            variant="destructive"
            disabled={remove.isPending}
            onClick={() => remove.mutate()}
          >
            <SteadyLabel
              busy={remove.isPending}
              idle="Remove course"
              working="Removing…"
            />
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
