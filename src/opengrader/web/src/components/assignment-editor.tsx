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
  type ComponentProps,
  type Ref,
} from 'react';
import { Link } from '@tanstack/react-router';
import { LoaderCircle } from 'lucide-react';
import { PageHeading } from '@/components/page-heading';
import { FileUpload } from '@/components/file-upload';
import { AssignmentPanes } from '@/components/assignment-panes';
import { AssignmentPreview } from '@/components/assignment-preview';
import { AssignmentActions } from '@/components/assignment-actions';
import { QAOutlineRail } from '@/components/qa-outline';
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
import {
  useAssignmentDraft,
  type AssignmentDraftOptions,
} from '@/hooks/use-assignment-draft';
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

export function AssignmentEditor(props: AssignmentDraftOptions) {
  const { assignmentId, overview } = props;
  const {
    source,
    originalSource,
    result,
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
  const updateActiveKey = useCallback((key: string | null) => {
    outlineRailRef.current?.setActiveKey(key);
  }, []);

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
    disabled: busy,
    editorId: mode === 'visual' ? 'visual-editor' : 'qaMarkdown',
    showVersionHeadings: mode === 'markdown',
    onVersionSelect: (version: string) => {
      const firstQuestion = outlineEntries.find(
        (entry) => entry.version === version
      );
      if (!valid || !firstQuestion || firstQuestion.line == null) return;
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
      disabled={busy || visualPending}
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
          void save();
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
                <Button type="submit" disabled={!valid || busy}>
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
                to="/assignments"
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
            to="/assignments/$assignmentId"
            params={{ assignmentId: saved.id }}
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
                  {result?.error && !visualPending && (
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
                              onClick={retryValidation}
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
                              disabled={readOnly || busy}
                              scrollElementRef={
                                overview ? editorScrollRef : undefined
                              }
                              onPending={markVisualPending}
                              onResult={acceptVisualEdit}
                            />
                          ) : source.trim() ? (
                            <div className="space-y-3">
                              <p role="status">
                                {result?.error || 'Reading assignment...'}
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
                                  edit(
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
                        <Field
                          data-invalid={!!result?.error}
                          className={overview ? 'min-h-0 flex-1' : undefined}
                        >
                          <div
                            className={overview ? 'min-h-0 flex-1' : undefined}
                          >
                            <MarkdownEditor
                              ref={editorRef}
                              id="qaMarkdown"
                              value={source}
                              readOnly={readOnly}
                              disabled={busy}
                              onChange={edit}
                              invalid={!!result?.error}
                              describedBy={
                                result?.error ? 'qa-error' : undefined
                              }
                              errorLine={markdownErrorLine}
                              navigation={markdownNavigation}
                              outlineEntries={outlineEntries}
                              active={mode === 'markdown' && valid}
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
              current={valid}
              blocked={busy || visualPending}
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
              disabled={!valid || busy || !dirty}
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
            onDeleted={allowLeave}
          />
        </footer>
      )}
    </div>
  );
}
