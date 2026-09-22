import { ErrorAlert } from '@/components/error-alert';
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
import { FileUpload } from '@/components/file-upload';
import { EditorResizeHandle } from '@/components/editor-resize-handle';
import { AssignmentPanes } from '@/components/assignment-panes';
import { AssignmentPreview } from '@/components/assignment-preview';
import { AssignmentActions } from '@/components/assignment-actions';
import { useSidebar } from '@/components/ui/sidebar';
import { QAOutlineRail } from '@/components/qa-outline';
import { useMarkdownOutline } from '@/hooks/use-markdown-outline';
import { jumpToSourceLine } from '@/lib/textarea-navigation';
import { Button } from '@/components/ui/button';
import { Field, FieldError } from '@/components/ui/field';
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

type EditorProps =
  | { assignmentId?: never; initialSource?: never; overview?: never }
  | {
      assignmentId: string;
      initialSource: string;
      overview: {
        assignment: Assignment;
        qa: QAPreview;
        editUnavailableReason?: string;
      };
    };

export default function NewAssignmentPage() {
  return <AssignmentEditor />;
}

export function AssignmentEditor({
  assignmentId,
  initialSource = '',
  overview,
}: EditorProps) {
  const { state: sidebarState, isMobile } = useSidebar();
  const router = useRouter();
  const [originalSource, setOriginalSource] = useState(initialSource);
  const [result, setResult] = useState<PreviewResult | undefined>(() =>
    overview ? { source: initialSource, data: overview.qa } : undefined
  );
  const [outline, setOutline] = useState<QAPreview | undefined>(overview?.qa);
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
  const [uploadedFileName, setUploadedFileName] = useState('');
  const [retry, setRetry] = useState(0);
  const [error, setError] = useState('');
  const [saveMessage, setSaveMessage] = useState('');
  const [uploadError, setUploadError] = useState('');
  const [reading, setReading] = useState(false);
  const [mode, setMode] = useState(overview ? 'markdown' : 'visual');
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
  const readOnly = !!overview?.editUnavailableReason;
  const current = result?.source === source;
  const valid = current && !!result.data && !!source.trim() && !visualPending;
  const dirty =
    !saved && (source !== originalSource || reading || visualPending);

  const acceptVisualEdit = useCallback(
    (edit: VisualEditResult) => {
      setSaveMessage('');
      form.setFieldValue('qaMarkdown', edit.qa_markdown);
      if (edit.preview) setOutline(edit.preview);
      if (edit.validation_error) {
        setResult({
          source: edit.qa_markdown,
          error: edit.validation_error,
        });
      } else if (edit.preview) {
        setResult({ source: edit.qa_markdown, data: edit.preview });
      } else {
        setResult({
          source: edit.qa_markdown,
          error: 'Check the rubric values.',
        });
      }
      setVisualPending(false);
    },
    [form]
  );

  useBlocker({
    shouldBlockFn: () => {
      if (savedRef.current) return false;
      if (savingRef.current) return true;
      if (!dirty) return false;
      if (!window.confirm('Discard unsaved changes?')) return true;
      if (overview) {
        form.setFieldValue('qaMarkdown', originalSource);
        setResult(undefined);
        setOutline(undefined);
        setMode('markdown');
        setVisualPending(false);
        setUploadedFileName('');
        setError('');
        setUploadError('');
      }
      return false;
    },
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
    setUploadError('');
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
        setUploadError(
          'Could not read this file. Choose a Markdown file or paste its content into the editor.'
        );
    } finally {
      if (id === fileRead.current) setReading(false);
    }
  }

  async function create(qaMarkdown: string) {
    if (
      !valid ||
      readOnly ||
      (overview && !dirty) ||
      reading ||
      savingRef.current ||
      savedRef.current
    )
      return;
    savingRef.current = true;
    setError('');
    setSaveMessage('');
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
    if (overview) setOriginalSource(qaMarkdown);
    else setSaved(assignment);
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
      if (overview) {
        await router.invalidate();
        setSaveMessage('Changes saved.');
      } else {
        await router.navigate({
          to: '/assignment',
          search: { assignment: assignment.id },
        });
      }
    } catch {
      setError(
        overview
          ? 'Assignment saved, but the overview could not refresh. Reload this page.'
          : 'Assignment saved, but its page could not open. Use the link below to try again.'
      );
    } finally {
      savingRef.current = false;
      if (overview) savedRef.current = false;
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
  const visibleOutlineEntries =
    mode === 'visual'
      ? outlineEntries.filter((entry) => entry.version === visualVersion)
      : outlineEntries;
  const outlineProps = {
    entries: visibleOutlineEntries,
    activeKey,
    outdated: mode === 'markdown' && !valid,
    disabled: reading || saving || !!saved,
    editorId: mode === 'visual' ? 'visual-editor' : 'qaMarkdown',
    showVersionHeadings: mode === 'markdown',
    onVersionSelect: (version: string) => {
      const editor = editorRef.current;
      const firstQuestion = outlineEntries.find(
        (entry) => entry.version === version
      );
      if (!valid || !editor || !firstQuestion || firstQuestion.line == null)
        return;
      const firstQuestionLine = firstQuestion.line;
      const lines = source.split(
        // oxlint-disable-next-line no-control-regex -- Match the QA parser's source lines.
        /\r\n|[\n\r\v\f\u001c-\u001e\u0085\u2028\u2029]/
      );
      const heading = lines.findLastIndex((line, index) => {
        if (index >= firstQuestionLine - 1) return false;
        const match = /^\s*##\s+(.+?)\s*$/.exec(line);
        return (
          match?.[1]
            .trim()
            .replace(/^version\s+/i, '')
            .trim() === version
        );
      });
      if (heading < 0) return;
      jumpToSourceLine(editor, heading + 1);
      setActiveKey(firstQuestion.key);
    },
    onSelect: (entry: OutlineEntry) => {
      if (reading || saving || saved) return;
      if (mode === 'visual') visualEditorRef.current?.navigate(entry);
      else if (valid && editorRef.current && entry.line !== null)
        jumpToSourceLine(editorRef.current, entry.line);
      else return;
      setActiveKey(entry.key);
    },
  };
  const uploadControl = !readOnly && (
    <FileUpload
      compact={!!overview}
      title={overview ? 'Replace answer key' : 'Import answer key'}
      description={
        overview
          ? 'Drop a Markdown file here to replace the editor content, or select one.'
          : 'Drop a Markdown file here or select one.'
      }
      buttonLabel={
        reading
          ? 'Reading file…'
          : uploadError
            ? 'Choose another file'
            : overview
              ? 'Replace answer key'
              : 'Select Markdown file'
      }
      inputLabel="Upload QA file"
      accept=".md,text/markdown,text/plain"
      disabled={reading || saving || !!saved || visualPending}
      error={uploadError}
      onFiles={(files) => void upload(files[0])}
    >
      <span
        role="status"
        className={`block wrap-break-word text-muted-foreground empty:hidden ${overview ? 'mt-1 text-xs' : 'mt-3 text-sm'}`}
        title={uploadedFileName || undefined}
      >
        {reading
          ? 'Reading file…'
          : uploadedFileName && `Imported ${uploadedFileName}`}
      </span>
    </FileUpload>
  );

  return (
    <div
      className={
        overview ? 'flex min-h-0 flex-1 flex-col overflow-hidden' : undefined
      }
    >
      <div className="@container">
        <div
          className={
            overview
              ? 'mb-4'
              : 'mb-8 flex flex-col gap-6 @min-[40rem]:flex-row @min-[40rem]:items-start'
          }
        >
          <PageHeading
            className="mb-0 min-w-0 flex-1"
            title={overview ? 'Overview' : 'Create assignment'}
            description={
              overview
                ? overview.assignment.title
                : 'Start an assignment or import a Markdown answer key.'
            }
            actions={overview ? uploadControl : undefined}
          />
          {!overview && uploadControl && (
            <div className="w-full shrink-0 @min-[40rem]:w-64">
              {uploadControl}
            </div>
          )}
        </div>
      </div>
      <form
        id={overview ? 'assignment-form' : undefined}
        onSubmit={(event) => {
          event.preventDefault();
          void form.handleSubmit();
        }}
        className={
          overview
            ? 'flex min-h-0 flex-1 flex-col space-y-8 overflow-hidden pb-16'
            : 'space-y-8'
        }
      >
        {(!overview || saving || saveMessage || readOnly) && (
          <div className="flex flex-wrap items-center gap-4">
            {readOnly ? (
              <p className="text-muted-foreground">
                {overview?.editUnavailableReason}
              </p>
            ) : (
              !overview && (
                <Button
                  type="submit"
                  disabled={!valid || reading || saving || !!saved}
                >
                  {saving && (
                    <LoaderCircle
                      className="motion-safe:animate-spin"
                      aria-hidden="true"
                    />
                  )}
                  Create assignment
                </Button>
              )
            )}
            {!overview && (
              <Link
                to="/"
                className="inline-flex min-h-11 items-center underline"
              >
                Back to assignments
              </Link>
            )}
            {saving && (
              <span role="status">Saving assignment. Keep this page open.</span>
            )}
            {saveMessage && <span role="status">{saveMessage}</span>}
          </div>
        )}
        {error && <ErrorAlert error={error} />}
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
          contained={!!overview}
          renderEditor={(previewToggle) => (
            <section
              className={`@container min-w-0 space-y-4 ${overview ? 'flex h-full min-h-0 flex-col' : ''}`}
              aria-labelledby={overview ? undefined : 'editor-heading'}
              aria-label={overview ? 'Assignment editor' : undefined}
            >
              {!overview && (
                <div className="flex flex-col items-start gap-3 @min-[24rem]:flex-row @min-[24rem]:items-center @min-[24rem]:justify-between">
                  <h2 id="editor-heading" className="text-lg font-semibold">
                    Assignment editor
                  </h2>
                  {previewToggle}
                </div>
              )}
              <Tabs
                className={overview ? 'min-h-0 flex-1' : undefined}
                value={mode}
                onValueChange={(value) => {
                  setMode(String(value));
                  setActiveKey(null);
                }}
              >
                <div
                  className={`sticky top-0 z-20 flex flex-wrap items-center justify-between gap-x-2 border-b bg-background ${overview ? 'pb-2' : ''}`}
                >
                  <TabsList aria-label="Editor mode" className="border-b-0">
                    <TabsTrigger value="visual">Visual</TabsTrigger>
                    <TabsTrigger value="markdown" disabled={visualPending}>
                      Markdown
                    </TabsTrigger>
                  </TabsList>
                  <div className="flex items-center gap-2">
                    {visibleOutlineEntries.length > 1 && (
                      <QAOutlineRail {...outlineProps} />
                    )}
                    {overview && previewToggle}
                  </div>
                </div>
                <div
                  className={
                    overview
                      ? 'min-h-0 flex-1 overflow-y-auto overscroll-contain'
                      : undefined
                  }
                >
                  <div
                    className={
                      overview
                        ? 'flex h-full min-h-0 min-w-0 flex-col'
                        : 'min-w-0'
                    }
                  >
                    <TabsContent value="visual">
                      {outline ? (
                        <VisualAssignmentEditor
                          ref={visualEditorRef}
                          onOutlineChange={acceptOutline}
                          onActiveChange={setActiveKey}
                          source={source}
                          preview={outline}
                          disabled={readOnly || reading || saving || !!saved}
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
                        <ErrorAlert className="mt-4" error={result.error} />
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
                    <TabsContent
                      value="markdown"
                      className={
                        overview ? 'flex min-h-0 flex-1 flex-col' : undefined
                      }
                    >
                      <form.Field name="qaMarkdown">
                        {(field) => (
                          <Field
                            data-invalid={current && !!result.error}
                            className={overview ? 'min-h-0 flex-1' : undefined}
                          >
                            <div
                              className={
                                overview ? 'min-h-0 flex-1' : undefined
                              }
                            >
                              <Textarea
                                ref={editorRef}
                                id={field.name}
                                name={field.name}
                                aria-label="QA markdown"
                                value={field.state.value}
                                readOnly={readOnly}
                                disabled={reading || saving || !!saved}
                                onBlur={field.handleBlur}
                                onSelect={markdownNavigation.onSelect}
                                onScroll={markdownNavigation.onScroll}
                                onChange={(event) => {
                                  field.handleChange(event.target.value);
                                  setError('');
                                  setSaveMessage('');
                                }}
                                spellCheck={false}
                                aria-invalid={current && !!result.error}
                                aria-describedby={
                                  current && result.error
                                    ? 'qa-error'
                                    : undefined
                                }
                                className={`field-sizing-fixed min-h-48 resize-none overflow-y-auto p-4 font-mono text-base leading-relaxed md:text-base ${overview ? 'h-[calc(100%-1.5rem)]' : 'h-[min(28rem,70svh)]'}`}
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
          )}
          preview={
            <AssignmentPreview
              source={source}
              preview={outline}
              current={!!valid}
              blocked={reading || saving || !!saved || visualPending}
              showGuide={!!overview}
              savedAssignment={
                overview && assignmentId
                  ? { id: assignmentId, source: originalSource }
                  : undefined
              }
            />
          }
        />
      </form>
      {overview && (
        <footer
          className="fixed bottom-0 z-30 flex justify-end gap-3 border-t bg-background px-4 py-3 sm:px-8"
          style={{
            insetInlineStart: isMobile
              ? 0
              : `var(--sidebar-width${sidebarState === 'collapsed' ? '-icon' : ''})`,
            width: isMobile
              ? '100%'
              : `calc(100% - var(--sidebar-width${sidebarState === 'collapsed' ? '-icon' : ''}))`,
          }}
        >
          {!readOnly && (
            <Button
              type="submit"
              form="assignment-form"
              disabled={!valid || reading || saving || !!saved || !dirty}
            >
              {saving && (
                <LoaderCircle
                  className="motion-safe:animate-spin"
                  aria-hidden="true"
                />
              )}
              Save changes
            </Button>
          )}
          <AssignmentActions
            assignment={overview.assignment}
            onDeleted={() => {
              savedRef.current = true;
            }}
          />
        </footer>
      )}
    </div>
  );
}
