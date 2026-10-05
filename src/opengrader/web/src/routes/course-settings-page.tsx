import { useRef, useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { LoaderCircle } from 'lucide-react';
import {
  coursesQuery,
  updateCourse,
  workspaceQuery,
  type WorkspaceInfo,
} from '@/api/workspace';
import { CourseFields } from '@/components/course-fields';
import {
  courseDraftErrors,
  type CourseDraft,
  type CourseDraftErrors,
} from '@/lib/course-draft';
import { ErrorAlert } from '@/components/error-alert';
import { PageHeading } from '@/components/page-heading';
import { SteadyLabel } from '@/components/steady-label';
import { RemoveCourseButton } from '@/components/remove-course-button';
import { Button } from '@/components/ui/button';
import { courseSettingsRoute } from '@/router';

function draftFrom(workspace: WorkspaceInfo): CourseDraft {
  return {
    number: workspace.course?.number ?? '',
    name: workspace.course?.name ?? '',
    term: workspace.course?.term ?? null,
    year: workspace.course?.year ?? null,
  };
}

export default function CourseSettingsPage() {
  const queryClient = useQueryClient();
  const { data: workspace } = useQuery({
    ...workspaceQuery,
    initialData: courseSettingsRoute.useLoaderData(),
  });
  const [draft, setDraft] = useState(() => draftFrom(workspace));
  const [errors, setErrors] = useState<CourseDraftErrors>({});
  const [saved, setSaved] = useState(false);
  const numberInput = useRef<HTMLInputElement>(null);
  const nameInput = useRef<HTMLInputElement>(null);
  const save = useMutation({
    mutationFn: () =>
      updateCourse(workspace.id, {
        ...draft,
        number: draft.number.trim(),
        name: draft.name.trim(),
      }),
    onSuccess: async () => {
      setSaved(true);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: workspaceQuery.queryKey }),
        queryClient.invalidateQueries({ queryKey: coursesQuery.queryKey }),
      ]);
    },
  });
  function submit(event: FormEvent) {
    event.preventDefault();
    if (save.isPending) return;
    const next = courseDraftErrors(draft);
    setErrors(next);
    if (next.number) return numberInput.current?.focus();
    if (next.name) return nameInput.current?.focus();
    save.mutate();
  }
  return (
    <>
      <PageHeading title="Course settings" />
      <form noValidate onSubmit={submit} className="grid max-w-xl gap-4">
        <CourseFields
          draft={draft}
          onChange={(next) => {
            setDraft(next);
            setErrors({});
            setSaved(false);
            if (save.isError) save.reset();
          }}
          errors={errors}
          readOnly={save.isPending}
          numberInput={numberInput}
          nameInput={nameInput}
        />
        {save.isError && (
          <ErrorAlert
            title="The course could not be saved."
            error={save.error}
          />
        )}
        <div className="flex items-center gap-3">
          <Button type="submit" disabled={save.isPending}>
            <SteadyLabel
              busy={save.isPending}
              idle="Save changes"
              working={
                <>
                  <LoaderCircle
                    aria-hidden="true"
                    className="motion-safe:animate-spin"
                  />
                  Saving…
                </>
              }
            />
          </Button>
          <p role="status" className="text-sm text-muted-foreground">
            {saved && 'Changes saved.'}
          </p>
        </div>
      </form>
      {workspace.course && (
        <section className="mt-12 max-w-xl border-t pt-6">
          <h2 className="text-base font-semibold">Remove course</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Its files and grades won't be deleted.
          </p>
          <RemoveCourseButton
            className="mt-4"
            course={{ id: workspace.id, number: workspace.course.number }}
            onRemoved={() => window.location.assign('/courses')}
          />
        </section>
      )}
    </>
  );
}
