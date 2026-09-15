import { errorMessage } from '@/lib/error-message';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useForm, useSelector } from '@tanstack/react-form';
import { Link, useBlocker, useRouter } from '@tanstack/react-router';
import { LoaderCircle } from 'lucide-react';
import {
  assignmentsQuery,
  createAssignment,
  updateAssignment,
  previewAssignment,
  type Assignment,
  type QAPreview,
  type VisualEditResult,
} from '@/api/queries';
import { PageHeading } from '@/components/page-heading';
import { EditorResizeHandle } from '@/components/editor-resize-handle';
import { AssignmentPanes } from '@/components/assignment-panes';
import { AssignmentPreview } from '@/components/assignment-preview';
import { QAOutlineRail } from '@/components/qa-outline';
import { useMarkdownOutline } from '@/hooks/use-markdown-outline';
import { jumpToSourceLine } from '@/lib/textarea-navigation';
import { Button } from '@/components/ui/button';
import { Field, FieldLabel, FieldError } from '@/components/ui/field';
import { Textarea } from '@/components/ui/textarea';
import { schedulePreview } from '@/lib/debounced-preview';
import {
  VisualAssignmentEditor,
  type VisualEditorNavigation,
} from '@/components/visual-assignment-editor';
import {
  previewOutlineGroups,
  questionOutlineEntries,
  type OutlineEntry,
} from '@/lib/assignment-outline';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';

type PreviewResult = { source: string } & (
  | { data: QAPreview; error?: never }
  | { error: string; data?: never }
);

export default function NewAssignmentPage() {
  return <AssignmentEditor />;
}

export function AssignmentEditor({
  assignmentId,
  initialSource = '',
}: {
  assignmentId?: string;
  initialSource?: string;
}) {
  const router = useRouter();
  const [originalSource] = useState(initialSource);
  const [result, setResult] = useState<PreviewResult>();
  const [outline, setOutline] = useState<QAPreview>();
  const editorRef = useRef<HTMLTextAreaElement>(null);
  const visualEditorRef = useRef<VisualEditorNavigation>(null);
  const [visualOutline, setVisualOutline] = useState<OutlineEntry[]>([]);
  const [visualVersion, setVisualVersion] = useState('');
  const acceptOutline = useCallback(
    (entries: OutlineEntry[], version: string) => {
      setVisualOutline(entries);
      setVisualVersion(version);
    },
    []
  );
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploadedFileName, setUploadedFileName] = useState('');
  const [retry, setRetry] = useState(0);
  const [error, setError] = useState('');
  const [reading, setReading] = useState(false);
  const [mode, setMode] = useState('visual');
  const [visualPending, setVisualPending] = useState(false);
  const [saved, setSaved] = useState<Assignment>();
  const savedRef = useRef(false);
  const savingRef = useRef(false);
  const fileRead = useRef(0);
  const form = useForm({
    defaultValues: { qaMarkdown: originalSource },
    onSubmit: async ({ value }) => {
      await create(value.qaMarkdown);
    },
  });
  const source = useSelector(form.store, (state) => state.values.qaMarkdown);
  const saving = useSelector(form.store, (state) => state.isSubmitting);
  const current = result?.source === source;
  const valid = current && !!result.data && !!source.trim() && !visualPending;
  const dirty =
    !saved && (source !== originalSource || reading || visualPending);

  const acceptVisualEdit = useCallback(
    (edit: VisualEditResult) => {
      form.setFieldValue('qaMarkdown', edit.qa_markdown);
      if (edit.preview) {
        setOutline(edit.preview);
        setResult({ source: edit.qa_markdown, data: edit.preview });
      } else {
        setResult({
          source: edit.qa_markdown,
          error: edit.validation_error ?? 'Check the rubric values.',
        });
      }
      setVisualPending(false);
    },
    [form]
  );

  useBlocker({
    shouldBlockFn: () =>
      !savedRef.current &&
      (savingRef.current ||
        (dirty && !window.confirm('Discard unsaved changes?'))),
    enableBeforeUnload: () => !savedRef.current && (dirty || savingRef.current),
  });

  useEffect(() => {
    if (!source.trim() || current) return;
    return schedulePreview(
      (signal) => previewAssignment(source, signal),
      (response) => {
        if ('data' in response) setOutline(response.data);
        setResult(
          'data' in response
            ? { source, data: response.data }
            : { source, error: errorMessage(response.error) }
        );
      }
    );
  }, [source, retry, current]);

  useEffect(
    () => () => {
      fileRead.current += 1;
    },
    []
  );

  async function upload(file: File | undefined) {
    if (!file) return;
    const id = ++fileRead.current;
    setReading(true);
    setError('');
    try {
      const text = new TextDecoder('utf-8', { fatal: true }).decode(
        await file.arrayBuffer()
      );
      if (id === fileRead.current) {
        setResult(undefined);
        setMode('markdown');
        form.setFieldValue('qaMarkdown', text);
        setUploadedFileName(file.name);
        setRetry((value) => value + 1);
      }
    } catch {
      if (id === fileRead.current)
        setError(
          'Could not read this file. Select a markdown file or paste its content into the editor.'
        );
    } finally {
      if (id === fileRead.current) setReading(false);
    }
  }

  async function create(qaMarkdown: string) {
    if (!valid || reading || savingRef.current || savedRef.current) return;
    savingRef.current = true;
    setError('');
    let assignment: Assignment;
    try {
      assignment = assignmentId
        ? await updateAssignment(assignmentId, qaMarkdown, originalSource)
        : await createAssignment(qaMarkdown);
    } catch (cause) {
      setError(errorMessage(cause));
      savingRef.current = false;
      return;
    }
    savedRef.current = true;
    setSaved(assignment);
    try {
      const queryClient = router.options.context.queryClient;
      await queryClient.cancelQueries({
        queryKey: assignmentsQuery.queryKey,
        exact: true,
      });
      queryClient.setQueryData(assignmentsQuery.queryKey, (previous) => [
        ...(previous ?? []).filter((item) => item.id !== assignment.id),
        assignment,
      ]);
      await queryClient.cancelQueries({
        queryKey: ['assignments', assignment.id],
      });
      queryClient.removeQueries({ queryKey: ['assignments', assignment.id] });
      await queryClient.invalidateQueries({
        queryKey: assignmentsQuery.queryKey,
        exact: true,
      });
      await router.navigate({
        to: '/assignment',
        search: { assignment: assignment.id },
      });
    } catch {
      setError(
        'Assignment saved, but its page could not open. Use the link below to try again.'
      );
    } finally {
      savingRef.current = false;
    }
  }

  const outlineEntries = useMemo(
    () =>
      mode === 'visual'
        ? visualOutline
        : outline
          ? questionOutlineEntries(previewOutlineGroups(outline.document))
          : [],
    [mode, visualOutline, outline]
  );
  const markdownNavigation = useMarkdownOutline(
    editorRef,
    outlineEntries,
    mode === 'markdown' && !!valid,
    setActiveKey
  );
  const currentVersion =
    mode === 'visual'
      ? visualVersion
      : (outlineEntries.find((entry) => entry.key === activeKey)?.version ??
        outlineEntries[0]?.version);
  const visibleOutlineEntries = outlineEntries.filter(
    (entry) => entry.version === currentVersion
  );
  const outlineProps = {
    entries: visibleOutlineEntries,
    activeKey,
    outdated: mode === 'markdown' && !valid,
    disabled: reading || saving || !!saved,
    editorId: mode === 'visual' ? 'visual-editor' : 'qaMarkdown',
    onSelect: (entry: OutlineEntry) => {
      if (reading || saving || saved) return;
      if (mode === 'visual') visualEditorRef.current?.navigate(entry);
      else if (valid && editorRef.current && entry.line !== null)
        jumpToSourceLine(editorRef.current, entry.line);
      else return;
      setActiveKey(entry.key);
    },
  };

  return (
    <>
      <PageHeading
        title={assignmentId ? 'Edit assignment' : 'Create assignment'}
        description={
          assignmentId
            ? 'Edit the assignment, then check the PDF before saving.'
            : 'Start an assignment or import a Markdown answer key.'
        }
      />
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void form.handleSubmit();
        }}
        className="space-y-8"
      >
        <div className="flex flex-wrap items-center gap-4">
          <Button
            type="submit"
            disabled={
              !valid ||
              reading ||
              saving ||
              !!saved ||
              (assignmentId !== undefined && !dirty)
            }
          >
            {saving && (
              <LoaderCircle
                className="motion-safe:animate-spin"
                aria-hidden="true"
              />
            )}
            {assignmentId ? 'Save changes' : 'Create assignment'}
          </Button>
          <Link to="/" className="inline-flex min-h-11 items-center underline">
            Back to assignments
          </Link>
          {saving && (
            <span role="status">Saving assignment. Keep this page open.</span>
          )}
        </div>
        {error && <p role="alert">{error}</p>}
        {saved && (
          <Link
            to="/assignment"
            search={{ assignment: saved.id }}
            className="underline"
          >
            Open {saved.title}
          </Link>
        )}
        <AssignmentPanes
          editor={
            <section
              className="@container min-w-0 space-y-4"
              aria-labelledby="editor-heading"
            >
              <div className="space-y-2">
                <input
                  ref={fileInputRef}
                  type="file"
                  hidden
                  aria-label="Upload QA file"
                  accept=".md,text/markdown,text/plain"
                  disabled={reading || saving || !!saved}
                  onChange={(event) => {
                    void upload(event.target.files?.[0]);
                    event.target.value = '';
                  }}
                />
                <div className="flex flex-col items-start gap-3 @min-[24rem]:flex-row @min-[24rem]:items-center @min-[24rem]:justify-between">
                  <h2 id="editor-heading" className="text-lg font-semibold">
                    Assignment editor
                  </h2>
                  <Button
                    type="button"
                    variant="outline"
                    disabled={reading || saving || !!saved || visualPending}
                    onClick={() => fileInputRef.current?.click()}
                    className="shrink-0"
                  >
                    Upload
                  </Button>
                </div>
                <span
                  role="status"
                  className="block text-sm wrap-break-word text-muted-foreground empty:hidden"
                  title={uploadedFileName || undefined}
                >
                  {reading
                    ? 'Reading file…'
                    : uploadedFileName && `Imported ${uploadedFileName}`}
                </span>
              </div>
              <Tabs
                value={mode}
                onValueChange={(value) => {
                  setMode(String(value));
                  setActiveKey(null);
                }}
              >
                <div className="sticky top-0 z-20 flex flex-wrap items-center justify-between gap-x-2 border-b bg-background">
                  <TabsList aria-label="Editor mode" className="border-b-0">
                    <TabsTrigger value="visual">Visual</TabsTrigger>
                    <TabsTrigger value="markdown" disabled={visualPending}>
                      Markdown
                    </TabsTrigger>
                  </TabsList>
                  {visibleOutlineEntries.length > 1 && (
                    <div className="z-30 min-[65rem]:fixed min-[65rem]:inset-s-3 min-[65rem]:top-1/2 min-[65rem]:-translate-y-1/2">
                      <QAOutlineRail {...outlineProps} />
                    </div>
                  )}
                </div>
                <div>
                  <div className="min-w-0">
                    <TabsContent value="visual">
                      {outline ? (
                        <VisualAssignmentEditor
                          ref={visualEditorRef}
                          onOutlineChange={acceptOutline}
                          onActiveChange={setActiveKey}
                          source={source}
                          preview={outline}
                          disabled={reading || saving || !!saved}
                          onPending={() => setVisualPending(true)}
                          onResult={acceptVisualEdit}
                        />
                      ) : source.trim() ? (
                        <div className="space-y-3">
                          <p role="status">
                            {current && result.error
                              ? result.error
                              : 'Reading assignment...'}
                          </p>
                          <Button
                            type="button"
                            variant="outline"
                            onClick={() => setMode('markdown')}
                          >
                            Open Markdown
                          </Button>
                        </div>
                      ) : (
                        <div className="space-y-4 py-8">
                          <p>
                            Create your first question, or import an existing
                            answer key.
                          </p>
                          <Button
                            type="button"
                            variant="outline"
                            onClick={() =>
                              form.setFieldValue(
                                'qaMarkdown',
                                '---\ntitle: Untitled assignment\n---\n\n- (10 points) New question\n  - **Answer**\n    - Expected answer\n'
                              )
                            }
                          >
                            Start assignment
                          </Button>
                        </div>
                      )}
                      {outline && current && result.error && (
                        <p role="alert" className="mt-4">
                          {result.error}
                        </p>
                      )}
                      {visualPending && (
                        <p
                          role="status"
                          className="mt-4 text-sm text-muted-foreground"
                        >
                          Updating draft...
                        </p>
                      )}
                    </TabsContent>
                    <TabsContent value="markdown">
                      <form.Field name="qaMarkdown">
                        {(field) => (
                          <Field data-invalid={current && !!result.error}>
                            <div className="flex items-center justify-between gap-3">
                              <FieldLabel htmlFor={field.name}>
                                QA markdown
                              </FieldLabel>
                            </div>
                            <div>
                              <Textarea
                                ref={editorRef}
                                id={field.name}
                                name={field.name}
                                value={field.state.value}
                                disabled={reading || saving || !!saved}
                                onBlur={field.handleBlur}
                                onSelect={markdownNavigation.onSelect}
                                onScroll={markdownNavigation.onScroll}
                                onChange={(event) => {
                                  field.handleChange(event.target.value);
                                  setError('');
                                }}
                                spellCheck={false}
                                aria-invalid={current && !!result.error}
                                aria-describedby={
                                  current && result.error
                                    ? 'qa-error'
                                    : undefined
                                }
                                className="field-sizing-fixed h-[min(28rem,70svh)] min-h-48 resize-none overflow-y-auto p-4 font-mono text-base leading-relaxed md:text-base"
                                placeholder={
                                  '---\ntitle: Quiz 1\n---\n\n- (2 points) What is 1 + 1?\n  - **Correct**\n    - 2'
                                }
                              />
                              <EditorResizeHandle
                                editorRef={editorRef}
                                disabled={reading || saving || !!saved}
                              />
                            </div>
                            {current && result.error && (
                              <div className="space-y-2">
                                <FieldError id="qa-error">
                                  {result.error}
                                </FieldError>
                                <Button
                                  type="button"
                                  variant="outline"
                                  onClick={() => {
                                    setResult(undefined);
                                    setRetry((value) => value + 1);
                                  }}
                                >
                                  Retry validation
                                </Button>
                              </div>
                            )}
                          </Field>
                        )}
                      </form.Field>
                    </TabsContent>
                  </div>
                </div>
              </Tabs>
            </section>
          }
          preview={
            <AssignmentPreview
              source={source}
              preview={outline}
              current={!!valid}
              blocked={reading || saving || !!saved || visualPending}
            />
          }
        />
      </form>
    </>
  );
}
