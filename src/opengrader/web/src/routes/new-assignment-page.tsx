import { ErrorAlert } from '@/components/error-alert';
import { errorMessage } from '@/lib/error-message';
import {
  Activity,
  lazy,
  Suspense,
  startTransition,
  useCallback,
  useEffect,
  useImperativeHandle,
  memo,
  useMemo,
  useRef,
  useState,
  type ComponentProps,
  type Ref,
} from 'react';
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
import { AssignmentPanes } from '@/components/assignment-panes';
import { AssignmentPreview } from '@/components/assignment-preview';
import { AssignmentActions } from '@/components/assignment-actions';
import { QAOutlineRail } from '@/components/qa-outline';
import { Button } from '@/components/ui/button';
import { Field } from '@/components/ui/field';
import { schedulePreview } from '@/lib/debounced-preview';
import type { MarkdownEditorNavigation } from '@/components/markdown-editor';
import type { VisualEditorNavigation } from '@/components/visual-assignment-editor';
import {
  previewOutlineGroups,
  questionOutlineEntries,
  type OutlineEntry,
} from '@/lib/assignment-outline';
import { validationSourceLine } from '@/lib/validation-source-line';
import type { ValidationLocation } from '@/lib/validation-source-line';
import { ApiError } from '@/api/client';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';

const loadMarkdownEditor = () =>
  import('@/components/markdown-editor').then(({ MarkdownEditor }) => ({
    default: MarkdownEditor,
  }));
const loadVisualEditor = () =>
  import('@/components/visual-assignment-editor').then(
    ({ VisualAssignmentEditor }) => ({ default: VisualAssignmentEditor })
  );
const MarkdownEditor = lazy(loadMarkdownEditor);
const VisualAssignmentEditor = lazy(loadVisualEditor);

type PreviewResult = { source: string } & (
  | { data: QAPreview; error?: never }
  | { error: string; location?: ValidationLocation | null; data?: never }
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

type ActiveOutlineNavigation = {
  setActiveKey: (key: string | null) => void;
};

const ActiveQAOutlineRail = memo(function ActiveQAOutlineRail({
  ref,
  ...props
}: Omit<ComponentProps<typeof QAOutlineRail>, 'activeKey'> & {
  ref?: Ref<ActiveOutlineNavigation>;
}) {
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const updateActiveKey = useCallback((key: string | null) => {
    startTransition(() => {
      setActiveKey((current) => (current === key ? current : key));
    });
  }, []);
  useImperativeHandle(ref, () => ({ setActiveKey: updateActiveKey }), [
    updateActiveKey,
  ]);
  return <QAOutlineRail {...props} activeKey={activeKey} />;
});

export default function NewAssignmentPage() {
  return <AssignmentEditor />;
}

export function AssignmentEditor({
  assignmentId,
  initialSource = '',
  overview,
}: EditorProps) {
  const router = useRouter();
  const [originalSource, setOriginalSource] = useState(initialSource);
  const [result, setResult] = useState<PreviewResult | undefined>(() =>
    overview ? { source: initialSource, data: overview.qa } : undefined
  );
  const [outline, setOutline] = useState<QAPreview | undefined>(overview?.qa);
  const [visualBaseline, setVisualBaseline] = useState<
    { source: string; preview: QAPreview } | undefined
  >(() =>
    overview ? { source: initialSource, preview: overview.qa } : undefined
  );
  const editorRef = useRef<MarkdownEditorNavigation>(null);
  const [markdownNavigation, setMarkdownNavigation] = useState<{
    line: number;
  }>();
  const editorScrollRef = useRef<HTMLDivElement>(null);
  const visualEditorRef = useRef<VisualEditorNavigation>(null);
  const outlineRailRef = useRef<ActiveOutlineNavigation>(null);
  const [visualOutline, setVisualOutline] = useState<OutlineEntry[]>([]);
  const [visualVersion, setVisualVersion] = useState('');
  const acceptOutline = useCallback(
    (entries: OutlineEntry[], version: string) => {
      startTransition(() => {
        setVisualOutline(entries);
        setVisualVersion(version);
      });
    },
    []
  );
  const [uploadedFileName, setUploadedFileName] = useState('');
  const [retry, setRetry] = useState(0);
  const [error, setError] = useState('');
  const [saveMessage, setSaveMessage] = useState('');
  const [uploadError, setUploadError] = useState('');
  const [reading, setReading] = useState(false);
  const [mode, setMode] = useState<'markdown' | 'visual'>(
    overview ? 'markdown' : 'visual'
  );
  const [openedModes, setOpenedModes] = useState(() => ({
    markdown: !!overview,
    visual: !overview,
  }));
  const openMode = useCallback((nextMode: 'markdown' | 'visual') => {
    setOpenedModes((current) =>
      current[nextMode] ? current : { ...current, [nextMode]: true }
    );
    setMode(nextMode);
  }, []);
  const [visualPending, setVisualPending] = useState(false);
  const markVisualPending = useCallback(() => setVisualPending(true), []);
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
  const markdownErrorLine = validationSourceLine(
    current ? result?.error : undefined,
    current && result?.error ? result.location : null
  );
  const dirty =
    !saved && (source !== originalSource || reading || visualPending);
  const updateActiveKey = useCallback((key: string | null) => {
    outlineRailRef.current?.setActiveKey(key);
  }, []);

  const acceptVisualEdit = useCallback(
    (edit: VisualEditResult) => {
      setSaveMessage('');
      form.setFieldValue('qaMarkdown', edit.qa_markdown);
      if (edit.preview) setOutline(edit.preview);
      if (edit.validation_error) {
        setResult({
          source: edit.qa_markdown,
          error: edit.validation_error,
          location: edit.validation_location,
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
        openMode('markdown');
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
        if ('data' in response) {
          setOutline(response.data);
          startTransition(() => {
            setVisualBaseline({ source, preview: response.data });
          });
        } else if (outline) {
          startTransition(() => {
            setVisualBaseline({ source, preview: outline });
          });
        }
        setResult(
          'data' in response
            ? { source, data: response.data }
            : {
                source,
                error: errorMessage(response.error),
                location:
                  response.error instanceof ApiError
                    ? response.error.location
                    : null,
              }
        );
      },
      150
    );
  }, [source, retry, current, outline]);

  useEffect(
    () => () => {
      fileRead.current += 1;
    },
    []
  );

  useEffect(() => {
    const preload = overview ? loadVisualEditor : loadMarkdownEditor;
    const preloadInactiveEditor = () => {
      void preload().catch(() => undefined);
    };
    if ('requestIdleCallback' in window) {
      const idle = window.requestIdleCallback(preloadInactiveEditor, {
        timeout: 3000,
      });
      return () => window.cancelIdleCallback(idle);
    }
    const timer = globalThis.setTimeout(preloadInactiveEditor, 1000);
    return () => globalThis.clearTimeout(timer);
  }, [overview]);

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
        openMode('markdown');
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
  const visibleOutlineEntries = useMemo(
    () =>
      mode === 'visual'
        ? outlineEntries.filter((entry) => entry.version === visualVersion)
        : outlineEntries,
    [mode, outlineEntries, visualVersion]
  );
  const outlineProps = {
    entries: visibleOutlineEntries,
    outdated: mode === 'markdown' && !valid,
    disabled: reading || saving || !!saved,
    editorId: mode === 'visual' ? 'visual-editor' : 'qaMarkdown',
    showVersionHeadings: mode === 'markdown',
    onVersionSelect: (version: string) => {
      const firstQuestion = outlineEntries.find(
        (entry) => entry.version === version
      );
      if (!valid || !firstQuestion || firstQuestion.line == null) return;
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
      editorRef.current?.focusLine(heading + 1);
      updateActiveKey(firstQuestion.key);
    },
    onSelect: (entry: OutlineEntry) => {
      if (reading || saving || saved) return;
      if (mode === 'visual') visualEditorRef.current?.navigate(entry);
      else if (valid && entry.line !== null)
        editorRef.current?.focusLine(entry.line);
      else return;
      updateActiveKey(entry.key);
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
    <div className={overview ? 'flex min-h-0 flex-1 flex-col' : undefined}>
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
            ? 'flex min-h-0 flex-1 flex-col space-y-8 overflow-hidden pb-4'
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
                  if (value === 'visual' || value === 'markdown')
                    openMode(value);
                  updateActiveKey(null);
                }}
              >
                <div className="sticky top-0 z-20 shrink-0 bg-background">
                  <div
                    className={`flex flex-wrap items-center justify-between gap-x-2 border-b ${overview ? 'pb-2' : ''}`}
                  >
                    <TabsList aria-label="Editor mode" className="border-b-0">
                      <TabsTrigger value="visual">Visual</TabsTrigger>
                      <TabsTrigger value="markdown" disabled={visualPending}>
                        Markdown
                      </TabsTrigger>
                    </TabsList>
                    <div className="flex items-center gap-2">
                      {visibleOutlineEntries.length > 1 && (
                        <ActiveQAOutlineRail
                          ref={outlineRailRef}
                          {...outlineProps}
                        />
                      )}
                      {overview && previewToggle}
                    </div>
                  </div>
                  {current && result.error && !visualPending && (
                    <div id="qa-error" className="py-2">
                      <ErrorAlert error={result.error}>
                        <div className="flex flex-wrap gap-2">
                          {(markdownErrorLine !== null ||
                            !!result.location?.question_path?.length) && (
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              onClick={() => {
                                if (
                                  mode === 'visual' &&
                                  result.location &&
                                  visualEditorRef.current?.navigateError(
                                    result.location
                                  )
                                )
                                  return;
                                if (markdownErrorLine !== null) {
                                  setMarkdownNavigation({
                                    line: markdownErrorLine,
                                  });
                                  openMode('markdown');
                                }
                              }}
                            >
                              Go to error
                            </Button>
                          )}
                          {mode === 'markdown' && (
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              onClick={() => {
                                setResult(undefined);
                                setRetry((value) => value + 1);
                              }}
                            >
                              Retry validation
                            </Button>
                          )}
                        </div>
                      </ErrorAlert>
                    </div>
                  )}
                </div>
                <div
                  className={
                    overview ? 'flex min-h-0 flex-1 flex-col' : undefined
                  }
                >
                  <TabsContent
                    value="visual"
                    keepMounted
                    ref={editorScrollRef}
                    className={
                      overview
                        ? 'min-h-0 overflow-y-auto overscroll-contain'
                        : undefined
                    }
                  >
                    {openedModes.visual && (
                      <Suspense
                        fallback={
                          <p role="status" className="py-8">
                            Loading Visual editor…
                          </p>
                        }
                      >
                        <Activity
                          mode={mode === 'visual' ? 'visible' : 'hidden'}
                        >
                          {visualBaseline ? (
                            <VisualAssignmentEditor
                              key={visualBaseline.source}
                              ref={visualEditorRef}
                              onOutlineChange={acceptOutline}
                              onActiveChange={updateActiveKey}
                              source={visualBaseline.source}
                              preview={visualBaseline.preview}
                              disabled={
                                readOnly || reading || saving || !!saved
                              }
                              scrollElementRef={
                                overview ? editorScrollRef : undefined
                              }
                              onPending={markVisualPending}
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
                                onClick={() => openMode('markdown')}
                              >
                                Open Markdown
                              </Button>
                            </div>
                          ) : (
                            <div className="space-y-4 py-8">
                              <p>
                                Create your first question, or import an
                                existing answer key.
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
                          {visualPending && (
                            <p
                              role="status"
                              className="mt-4 text-sm text-muted-foreground"
                            >
                              Updating draft...
                            </p>
                          )}
                        </Activity>
                      </Suspense>
                    )}
                  </TabsContent>
                  <TabsContent
                    value="markdown"
                    keepMounted
                    className={
                      overview ? 'flex min-h-0 flex-1 flex-col' : undefined
                    }
                  >
                    {openedModes.markdown && (
                      <Suspense
                        fallback={
                          <p
                            role="status"
                            className="min-h-48 rounded-md border border-input p-4"
                          >
                            Loading Markdown editor…
                          </p>
                        }
                      >
                        <form.Field name="qaMarkdown">
                          {(field) => (
                            <Field
                              data-invalid={current && !!result.error}
                              className={
                                overview ? 'min-h-0 flex-1' : undefined
                              }
                            >
                              <div
                                className={
                                  overview ? 'min-h-0 flex-1' : undefined
                                }
                              >
                                <MarkdownEditor
                                  ref={editorRef}
                                  id={field.name}
                                  value={field.state.value}
                                  readOnly={readOnly}
                                  disabled={reading || saving || !!saved}
                                  onBlur={field.handleBlur}
                                  onChange={(value) => {
                                    field.handleChange(value);
                                    setError('');
                                    setSaveMessage('');
                                  }}
                                  invalid={current && !!result.error}
                                  describedBy={
                                    current && result.error
                                      ? 'qa-error'
                                      : undefined
                                  }
                                  errorLine={markdownErrorLine}
                                  navigation={markdownNavigation}
                                  outlineEntries={outlineEntries}
                                  active={mode === 'markdown' && !!valid}
                                  onActiveChange={updateActiveKey}
                                  className={
                                    overview
                                      ? 'h-full min-h-0'
                                      : 'h-[min(28rem,70svh)]'
                                  }
                                  placeholder={
                                    '---\ntitle: Quiz 1\n---\n\n- (2 points) What is 1 + 1?\n  - **Correct**\n    - 2'
                                  }
                                />
                              </div>
                            </Field>
                          )}
                        </form.Field>
                      </Suspense>
                    )}
                  </TabsContent>
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
        <footer className="z-30 -mx-4 flex shrink-0 justify-end gap-3 border-t bg-background px-4 py-3 sm:-mx-8 sm:px-8">
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
