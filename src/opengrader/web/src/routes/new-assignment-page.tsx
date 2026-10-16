import { useState } from 'react';
import { Link, useRouter } from '@tanstack/react-router';
import { FileText, LoaderCircle, PencilLine } from 'lucide-react';
import { createAssignmentFromTemplate } from '@/api/outline';
import { assignmentsQuery } from '@/api/queries';
import { errorMessage } from '@/lib/error-message';
import { newAssignmentRoute } from '@/router';
import { AssignmentEditor } from '@/components/assignment-editor';
import { ErrorAlert } from '@/components/error-alert';
import { FileUpload } from '@/components/file-upload';
import { PageHeading } from '@/components/page-heading';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

export default function NewAssignmentPage() {
  const { write } = newAssignmentRoute.useSearch();
  return write ? <AssignmentEditor /> : <TemplateForm />;
}

function TemplateForm() {
  const router = useRouter();
  const [title, setTitle] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [percent, setPercent] = useState<number | null>(null);
  const [error, setError] = useState('');

  function choose(files: File[]) {
    const chosen = files[0];
    if (!chosen) return;
    if (!chosen.name.toLowerCase().endsWith('.pdf')) {
      setError('Choose a PDF file.');
      return;
    }
    setError('');
    setFile(chosen);
    if (!title.trim()) setTitle(chosen.name.replace(/\.pdf$/i, ''));
  }

  async function create() {
    if (!file || !title.trim() || percent !== null) return;
    setError('');
    setPercent(0);
    try {
      const assignment = await createAssignmentFromTemplate(
        title.trim(),
        file,
        setPercent
      );
      const client = router.options.context.queryClient;
      client.setQueryData(assignmentsQuery.queryKey, (previous) => [
        ...(previous ?? []).filter((item) => item.id !== assignment.id),
        assignment,
      ]);
      void client.invalidateQueries({ queryKey: ['assignment-summaries'] });
      await router.navigate({
        to: '/assignments/$assignmentId/outline',
        params: { assignmentId: assignment.id },
      });
    } catch (cause) {
      setError(
        errorMessage(cause, 'Could not create the assignment. Try again.')
      );
      setPercent(null);
    }
  }

  return (
    <div className="custom-scrollbar min-h-0 flex-1 overflow-y-auto pb-8">
      <PageHeading
        title="Create assignment"
        description="Upload the blank PDF students write on. Next you draw a box around each question, and each student's answers are cut out of their scan for grading."
      />
      <form
        className="grid max-w-2xl gap-6"
        onSubmit={(event) => {
          event.preventDefault();
          void create();
        }}
      >
        <div className="grid gap-2">
          <Label htmlFor="assignment-title" className="text-sm">
            Assignment name
          </Label>
          <Input
            id="assignment-title"
            required
            maxLength={200}
            value={title}
            placeholder="Midterm 1"
            className="h-10 text-sm md:text-sm"
            onChange={(event) => setTitle(event.target.value)}
          />
        </div>
        <FileUpload
          title="Template PDF"
          description={
            file
              ? `${file.name} · ${(file.size / 1024 / 1024).toFixed(1)} MB`
              : 'Drop the assignment PDF here. It should be the same layout students receive.'
          }
          buttonLabel={file ? 'Choose a different PDF' : 'Select PDF'}
          inputLabel="Template PDF"
          accept=".pdf,application/pdf"
          disabled={percent !== null}
          onFiles={choose}
        >
          {file && (
            <p className="mt-3 flex items-center gap-2 text-sm">
              <FileText
                className="size-4 text-muted-foreground"
                aria-hidden="true"
              />
              <span className="truncate">{file.name}</span>
            </p>
          )}
        </FileUpload>
        {error && <ErrorAlert error={error} />}
        <div className="flex flex-wrap items-center gap-3">
          <Button
            type="submit"
            disabled={!file || !title.trim() || percent !== null}
          >
            {percent !== null && (
              <LoaderCircle
                className="motion-safe:animate-spin"
                aria-hidden="true"
              />
            )}
            {percent === null
              ? 'Create assignment'
              : percent < 100
                ? `Uploading ${percent}%`
                : 'Creating…'}
          </Button>
          <Button variant="ghost" render={<Link to="/assignments" />}>
            Cancel
          </Button>
        </div>
      </form>
      <div className="mt-10 grid max-w-2xl gap-2 border-t pt-6">
        <h2 className="font-medium">No PDF yet?</h2>
        <p className="text-muted-foreground">
          Write the questions, answers, and rubric in Opengrader. It makes a
          printable PDF with every box already drawn.
        </p>
        <Button
          variant="outline"
          className="justify-self-start"
          render={<Link to="/assignments/new" search={{ write: true }} />}
        >
          <PencilLine aria-hidden="true" />
          Write questions instead
        </Button>
      </div>
    </div>
  );
}
