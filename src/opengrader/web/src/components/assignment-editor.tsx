import { ErrorAlert } from '@/components/error-alert';
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
  type ReactNode,
  type Ref,
} from 'react';
import { Link } from '@tanstack/react-router';
import { CircleHelp, FileText, LoaderCircle, Plus, Upload } from 'lucide-react';
import { PageHeading } from '@/components/page-heading';
import { PaneLoading } from '@/components/loading';
import { AssignmentPanes, sidePanelId } from '@/components/assignment-panes';
import {
  AssignmentPreview,
  type SidePanelView,
} from '@/components/assignment-preview';
import { AssignmentActions } from '@/components/assignment-actions';
import { AnswerKeyView } from '@/components/answer-key-view';
import {
  QAOutlinePopover,
  QAOutlineRail,
  type OutlineProps,
} from '@/components/qa-outline';
import {
  ValidationStatus,
  validationMessageId,
} from '@/components/validation-status';
import { Button } from '@/components/ui/button';
import { Field } from '@/components/ui/field';
import type { MarkdownEditorNavigation } from '@/components/markdown-editor';
import type { VisualEditorNavigation } from '@/components/visual-assignment-editor';
import {
  previewOutlineGroups,
  questionOutlineEntries,
  versionHeadingLineNumber,
  type OutlineEntry,
} from '@/lib/assignment-outline';
import { markdownLineHints } from '@/lib/markdown-hints';
import { withoutSourceLines } from '@/lib/validation-source-line';
import {
  useAssignmentDraft,
  type AssignmentDraftOptions,
} from '@/hooks/use-assignment-draft';
import { setPreference } from '@/stores/preferences';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';

const railShownClassName = 'hidden @min-[46rem]:flex';
const railHiddenClassName = '@min-[46rem]:hidden';
const railGutterClassName = '@min-[46rem]:ps-4';

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

type ActiveOutlineNavigation = {
  setActiveKey: (key: string | null) => void;
};

const ActiveQAOutline = memo(function ActiveQAOutline({
  ref,
  variant,
  footer,
  ...props
}: Omit<OutlineProps, 'activeKey'> & {
  ref?: Ref<ActiveOutlineNavigation>;
  variant: 'popover' | 'rail';
  footer?: ReactNode;
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
  return variant === 'rail' ? (
    <QAOutlineRail {...props} activeKey={activeKey} footer={footer} />
  ) : (
    <QAOutlinePopover {...props} activeKey={activeKey} />
  );
});

export function AssignmentEditor(props: AssignmentDraftOptions) {
  const { assignmentId, overview } = props;
  const {
    source,
    originalSource,
    result,
    latestResult,
    checking,
    outline,
    visualBaseline,
    valid,
    dirty,
    readOnly,
    markdownErrorLine,
    mode,
    openedModes,
    openMode,
    visualPending,
    markVisualPending,
    acceptVisualEdit,
    failVisualEdit,
    visualFailure,
    edit,
    retryValidation,
    reading,
    uploadError,
    uploadedFileName,
    upload,
    busy,
    saving,
    saved,
    saveMessage,
    error,
    save,
    allowLeave,
  } = useAssignmentDraft(props);
  const editorRef = useRef<MarkdownEditorNavigation>(null);
  const [markdownNavigation, setMarkdownNavigation] = useState<{
    line: number;
  }>();
  const editorScrollRef = useRef<HTMLDivElement>(null);
  const [scrollReady, setScrollReady] = useState(false);
  const attachEditorScroll = useCallback((element: HTMLDivElement | null) => {
    editorScrollRef.current = element;
    setScrollReady(!!element);
  }, []);
  const visualEditorRef = useRef<VisualEditorNavigation>(null);
  const outlineRailRef = useRef<ActiveOutlineNavigation>(null);
  const outlinePopoverRef = useRef<ActiveOutlineNavigation>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [visualOutline, setVisualOutline] = useState<OutlineEntry[]>([]);
  const [visualVersion, setVisualVersion] = useState('');
  const [panelOpen, setPanelOpen] = useState(false);
  const [panelView, setPanelView] = useState<SidePanelView>('pdf');
  const [dragging, setDragging] = useState(false);
  const canImport = !readOnly && !busy && !visualPending;
  const acceptOutline = useCallback(
    (entries: OutlineEntry[], version: string) => {
      startTransition(() => {
        setVisualOutline(entries);
        setVisualVersion(version);
      });
    },
    []
  );
  const updateActiveKey = useCallback((key: string | null) => {
    outlineRailRef.current?.setActiveKey(key);
    outlinePopoverRef.current?.setActiveKey(key);
  }, []);

  const [initialMode] = useState(mode);
  useEffect(() => {
    const preload =
      initialMode === 'markdown' ? loadVisualEditor : loadMarkdownEditor;
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
  }, [initialMode]);

  const outlineEntries = useMemo(
    () =>
      mode === 'visual'
        ? visualOutline
        : outline
          ? questionOutlineEntries(previewOutlineGroups(outline.document))
          : [],
    [mode, visualOutline, outline]
  );
  const lineHints = useMemo(
    () => (outline ? markdownLineHints(outline) : []),
    [outline]
  );
  const latestError = latestResult?.error;
  const editorHints = useMemo(
    () =>
      valid
        ? lineHints
        : latestError
          ? markdownErrorLine === null
            ? []
            : [
                {
                  line: markdownErrorLine,
                  text: latestError,
                  tone: 'error' as const,
                },
              ]
          : null,
    [valid, lineHints, latestError, markdownErrorLine]
  );
  const visibleOutlineEntries = useMemo(
    () =>
      mode === 'visual'
        ? outlineEntries.filter((entry) => entry.version === visualVersion)
        : outlineEntries,
    [mode, outlineEntries, visualVersion]
  );
  const errorLocation = latestError ? latestResult?.location : null;
  const errorEntry = useMemo(() => {
    if (mode !== 'visual' || !errorLocation?.question_path?.length)
      return undefined;
    const number = errorLocation.question_path
      .map((index) => index + 1)
      .join('.');
    return outlineEntries.find(
      (entry) =>
        entry.versionLabel.trim() === errorLocation.version &&
        entry.number === number
    );
  }, [mode, errorLocation, outlineEntries]);
  const errorPlace =
    mode === 'markdown'
      ? markdownErrorLine === null
        ? null
        : `Line ${markdownErrorLine}`
      : errorEntry
        ? `${errorEntry.versionLabel ? `Version ${errorEntry.versionLabel}, question` : 'Question'} ${errorEntry.number}${errorLocation?.rubric_path ? ', rubric' : ''}`
        : null;
  const shownError =
    latestError && mode === 'visual'
      ? withoutSourceLines(latestError)
      : latestError;
  const errorGoTo = visualFailure
    ? undefined
    : errorEntry
      ? { label: 'Show question', onSelect: goToError }
      : markdownErrorLine !== null && !visualPending
        ? {
            label:
              mode === 'visual'
                ? 'Show in Markdown'
                : `Go to line ${markdownErrorLine}`,
            onSelect: goToError,
          }
        : undefined;
  function goToError() {
    if (
      mode === 'visual' &&
      latestResult?.location &&
      visualEditorRef.current?.navigateError(latestResult.location)
    )
      return;
    if (markdownErrorLine !== null && !visualPending) {
      setMarkdownNavigation({ line: markdownErrorLine });
      openMode('markdown');
    }
  }
  const outlineProps = {
    entries: visibleOutlineEntries,
    errorKey: errorEntry?.key ?? null,
    outdated: mode === 'markdown' && !!latestError,
    disabled: busy,
    editorId: mode === 'visual' ? 'visual-editor' : 'qaMarkdown',
    showVersionHeadings: mode === 'markdown',
    onVersionSelect: (version: string) => {
      const firstQuestion = outlineEntries.find(
        (entry) => entry.version === version
      );
      if (latestError || !firstQuestion || firstQuestion.line == null) return;
      const heading = versionHeadingLineNumber(
        source,
        version,
        firstQuestion.line
      );
      if (heading === null) return;
      editorRef.current?.focusLine(heading);
      updateActiveKey(firstQuestion.key);
    },
    onSelect: (entry: OutlineEntry) => {
      if (busy) return;
      if (mode === 'visual') visualEditorRef.current?.navigate(entry);
      else if (!latestError && entry.line !== null)
        editorRef.current?.focusLine(entry.line);
      else return;
      updateActiveKey(entry.key);
    },
  };

  const sideView: SidePanelView =
    mode === 'markdown' && !readOnly ? panelView : 'pdf';
  function togglePanel(view: SidePanelView) {
    if (panelOpen && sideView === view) setPanelOpen(false);
    else {
      setPanelView(view);
      setPanelOpen(true);
    }
  }
  const canAddQuestion = mode === 'visual' && !readOnly && !!visualBaseline;
  const showRail = visibleOutlineEntries.length > 0 || canAddQuestion;
  const importLabel = overview ? 'Replace answer key' : 'Import answer key';

  return (
    <div
      className="relative flex min-h-0 flex-1 flex-col"
      onDragOver={(event) => {
        if (!canImport || !event.dataTransfer.types.includes('Files')) return;
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null))
          setDragging(false);
      }}
      onDropCapture={(event) => {
        if (!event.dataTransfer.types.includes('Files')) return;
        event.preventDefault();
        event.stopPropagation();
        setDragging(false);
        if (canImport) void upload(event.dataTransfer.files[0]);
      }}
    >
      <PageHeading
        className="mb-8"
        title={overview ? 'Answer key' : 'Create assignment'}
        description={
          overview
            ? undefined
            : 'Build questions visually or write Markdown. You can also drop in an existing answer key.'
        }
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {!overview && (
              <Button
                type="button"
                variant="outline"
                disabled={!canImport}
                onClick={() => fileInputRef.current?.click()}
              >
                <Upload aria-hidden="true" />
                {reading ? 'Reading file…' : importLabel}
              </Button>
            )}
            {!readOnly && (
              <Button
                type="submit"
                form="assignment-form"
                disabled={!valid || busy || (!!overview && !dirty)}
              >
                {saving && (
                  <LoaderCircle
                    className="motion-safe:animate-spin"
                    aria-hidden="true"
                  />
                )}
                {overview ? 'Save changes' : 'Create assignment'}
              </Button>
            )}
            {overview && !overview.hideDelete && (
              <AssignmentActions
                assignment={overview.assignment}
                disabled={saving}
                onReplace={
                  readOnly ? undefined : () => fileInputRef.current?.click()
                }
                onDeleted={allowLeave}
              />
            )}
          </div>
        }
      />
      <input
        ref={fileInputRef}
        type="file"
        accept=".md,text/markdown,text/plain"
        disabled={!canImport}
        className="hidden"
        aria-label={importLabel}
        onChange={(event) => {
          void upload(event.target.files?.[0]);
          event.target.value = '';
        }}
      />
      <div className="space-y-3 empty:hidden">
        {readOnly && (
          <p className="text-muted-foreground">
            {overview?.editUnavailableReason} Showing the answer key as graders
            see it.
          </p>
        )}
        {!readOnly && overview?.structureLocked && (
          <p className="text-muted-foreground">
            The outline sets which questions exist. Edit answers and rubrics
            here; add, remove, or reorder questions on the{' '}
            <Link
              to="/assignments/$assignmentId/outline"
              params={{ assignmentId: overview.assignment.id }}
              className="text-foreground underline underline-offset-4"
            >
              Outline
            </Link>
            .
          </p>
        )}
        {(saving || saveMessage || reading || uploadedFileName) && (
          <p role="status" className="text-muted-foreground">
            {saving
              ? 'Saving assignment. Keep this page open.'
              : reading
                ? 'Reading file…'
                : saveMessage ||
                  `Imported ${uploadedFileName}. Review it, then ${overview ? 'save your changes' : 'create the assignment'}.`}
          </p>
        )}
        {uploadError && <ErrorAlert error={uploadError} />}
        {error && <ErrorAlert error={error} />}
        {saved && (
          <Link
            to="/assignments/$assignmentId"
            params={{ assignmentId: saved.id }}
            className="underline"
          >
            Open {saved.title}
          </Link>
        )}
      </div>
      <form
        id="assignment-form"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
        className="mt-4 flex min-h-0 flex-1 flex-col pb-4"
      >
        <AssignmentPanes
          open={panelOpen}
          onOpenChange={setPanelOpen}
          editor={
            <section
              className="@container flex h-full min-h-0 min-w-0 flex-col"
              aria-label="Assignment editor"
            >
              <Tabs
                className="min-h-0 flex-1"
                value={mode}
                onValueChange={(value) => {
                  if (value === 'visual' || value === 'markdown') {
                    openMode(value);
                    setPreference('editorMode', value);
                    if (value === 'visual' && sideView === 'format')
                      setPanelOpen(false);
                  }
                  updateActiveKey(null);
                }}
              >
                <div className="shrink-0">
                  <div className="flex flex-wrap items-center gap-2 border-b pb-2">
                    <TabsList aria-label="Editor mode" className="border-b-0">
                      <TabsTrigger value="visual">Visual</TabsTrigger>
                      <TabsTrigger value="markdown" disabled={visualPending}>
                        Markdown
                      </TabsTrigger>
                    </TabsList>
                    {!readOnly && (
                      <ValidationStatus
                        checking={checking}
                        error={visualFailure || shownError}
                        place={visualFailure ? null : errorPlace}
                        preview={latestResult?.data}
                        goTo={errorGoTo}
                        onRetry={
                          visualFailure
                            ? () => visualEditorRef.current?.retry()
                            : mode === 'markdown'
                              ? retryValidation
                              : undefined
                        }
                      />
                    )}
                    <div className="ms-auto flex flex-wrap items-center gap-1">
                      {visibleOutlineEntries.length > 0 && (
                        <div className={showRail ? railHiddenClassName : ''}>
                          <ActiveQAOutline
                            ref={outlinePopoverRef}
                            variant="popover"
                            {...outlineProps}
                          />
                        </div>
                      )}
                      <Button
                        type="button"
                        variant="ghost"
                        aria-expanded={panelOpen && sideView === 'pdf'}
                        aria-controls={sidePanelId}
                        onClick={() => togglePanel('pdf')}
                      >
                        <FileText aria-hidden="true" />
                        PDF
                      </Button>
                    </div>
                  </div>
                </div>
                <div className="flex min-h-0 flex-1">
                  {showRail && (
                    <aside
                      aria-label="Question outline"
                      className={`${railShownClassName} w-56 shrink-0 flex-col border-e pe-2 pb-2`}
                    >
                      <ActiveQAOutline
                        ref={outlineRailRef}
                        variant="rail"
                        {...outlineProps}
                        footer={
                          canAddQuestion && (
                            <Button
                              type="button"
                              variant="ghost"
                              className="w-full justify-start"
                              disabled={busy}
                              onClick={() =>
                                visualEditorRef.current?.addQuestion()
                              }
                            >
                              <Plus aria-hidden="true" />
                              Add question
                            </Button>
                          )
                        }
                      />
                    </aside>
                  )}
                  <div
                    className={`relative flex min-h-0 min-w-0 flex-1 flex-col ${showRail ? railGutterClassName : ''}`}
                  >
                    <TabsContent
                      value="visual"
                      keepMounted
                      ref={attachEditorScroll}
                      className="flex min-h-0 flex-1 flex-col overflow-y-auto overscroll-contain pe-4"
                    >
                      {openedModes.visual &&
                        scrollReady &&
                        (readOnly && outline ? (
                          <AnswerKeyView
                            ref={visualEditorRef}
                            preview={outline}
                            active={mode === 'visual'}
                            scrollElementRef={editorScrollRef}
                            onOutlineChange={acceptOutline}
                            onActiveChange={updateActiveKey}
                            rubricEditAssignmentId={
                              overview?.rubricEditable
                                ? overview.assignment.id
                                : undefined
                            }
                          />
                        ) : (
                          <Suspense
                            fallback={
                              <PaneLoading label="Loading Visual editor…" />
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
                                  disabled={readOnly || busy}
                                  structureLocked={!!overview?.structureLocked}
                                  scrollElementRef={editorScrollRef}
                                  onPending={markVisualPending}
                                  onResult={acceptVisualEdit}
                                  onFailure={failVisualEdit}
                                  errorKey={errorEntry?.key}
                                  validationError={shownError}
                                />
                              ) : source.trim() ? (
                                <div className="space-y-3 py-4">
                                  <p role="status">
                                    {result?.error || 'Reading assignment…'}
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
                                    Start with a blank question, or import an
                                    existing Markdown answer key.
                                  </p>
                                  <Button
                                    type="button"
                                    onClick={() =>
                                      edit(
                                        '---\ntitle: Untitled assignment\n---\n\n- (10 points) New question\n  - **Answer**\n    - Expected answer\n'
                                      )
                                    }
                                  >
                                    Start assignment
                                  </Button>
                                </div>
                              )}
                            </Activity>
                          </Suspense>
                        ))}
                    </TabsContent>
                    <TabsContent
                      value="markdown"
                      keepMounted
                      className="flex min-h-0 flex-1 flex-col"
                    >
                      {openedModes.markdown && (
                        <Suspense
                          fallback={
                            <PaneLoading label="Loading Markdown editor…" />
                          }
                        >
                          <Field
                            data-invalid={!!latestError}
                            className="min-h-0 flex-1"
                          >
                            <div className="min-h-0 flex-1">
                              <MarkdownEditor
                                ref={editorRef}
                                id="qaMarkdown"
                                value={source}
                                readOnly={readOnly}
                                disabled={busy}
                                onChange={edit}
                                invalid={!!latestError}
                                describedBy={
                                  latestError ? validationMessageId : undefined
                                }
                                errorLine={markdownErrorLine}
                                navigation={markdownNavigation}
                                outlineEntries={outlineEntries}
                                lineHints={editorHints}
                                active={mode === 'markdown' && valid}
                                onActiveChange={updateActiveKey}
                                className="h-full min-h-0"
                                placeholder={
                                  '---\ntitle: Quiz 1\n---\n\n- (2 points) What is 1 + 1?\n  - **Correct**\n    - 2'
                                }
                              />
                            </div>
                            {!readOnly && (
                              <div className="flex justify-end">
                                <Button
                                  type="button"
                                  variant="ghost"
                                  size="xs"
                                  aria-expanded={
                                    panelOpen && sideView === 'format'
                                  }
                                  aria-controls={sidePanelId}
                                  onClick={() => togglePanel('format')}
                                >
                                  <CircleHelp aria-hidden="true" />
                                  Format guide
                                </Button>
                              </div>
                            )}
                          </Field>
                        </Suspense>
                      )}
                    </TabsContent>
                    {dragging && (
                      <div
                        aria-hidden="true"
                        className="pointer-events-none absolute inset-0 z-30 flex items-center justify-center border-2 border-dashed border-ring bg-background/90 text-base font-medium"
                      >
                        Drop to {overview ? 'replace the' : 'import an'} answer
                        key
                      </div>
                    )}
                  </div>
                </div>
              </Tabs>
            </section>
          }
          panel={
            <AssignmentPreview
              source={source}
              preview={outline}
              current={valid}
              blocked={busy || visualPending}
              savedAssignment={
                overview && assignmentId
                  ? { id: assignmentId, source: originalSource }
                  : undefined
              }
              view={sideView}
              onClose={() => setPanelOpen(false)}
            />
          }
        />
      </form>
    </div>
  );
}
