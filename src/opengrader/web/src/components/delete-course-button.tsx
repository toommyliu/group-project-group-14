import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { deleteCourse } from '@/api/workspace';
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
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

export function DeleteCourseButton({
  course,
  onDeleted,
  className,
}: {
  course: { id: string; number: string };
  onDeleted: () => unknown;
  className?: string;
}) {
  const [typed, setTyped] = useState('');
  const remove = useMutation({
    mutationFn: () => deleteCourse(course.id, typed),
    onSuccess: onDeleted,
  });
  const confirmed = typed.trim() === course.number;
  return (
    <AlertDialog
      onOpenChange={(open, details) => {
        if (remove.isPending) return details.cancel();
        if (open) {
          remove.reset();
          setTyped('');
        }
      }}
    >
      <AlertDialogTrigger
        render={<Button variant="destructive" className={className} />}
      >
        Delete course
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle className="wrap-anywhere">
            Delete {course.number}?
          </AlertDialogTitle>
          <AlertDialogDescription>
            This permanently deletes its assignments, scans, submissions,
            grades, and roster. Any running processing stops after its current
            step. This cannot be undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <form
          id="delete-course"
          className="grid gap-1.5"
          onSubmit={(event) => {
            event.preventDefault();
            if (confirmed) remove.mutate();
          }}
        >
          <Label htmlFor="delete-course-number">
            Type {course.number} to confirm
          </Label>
          <Input
            id="delete-course-number"
            value={typed}
            autoComplete="off"
            disabled={remove.isPending}
            onChange={(event) => setTyped(event.target.value)}
          />
        </form>
        {remove.isError && <ErrorAlert error={remove.error} />}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={remove.isPending}>
            Cancel
          </AlertDialogCancel>
          <AlertDialogAction
            type="submit"
            form="delete-course"
            variant="destructive"
            disabled={!confirmed || remove.isPending}
          >
            <SteadyLabel
              busy={remove.isPending}
              idle="Delete course"
              working="Deleting…"
            />
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
