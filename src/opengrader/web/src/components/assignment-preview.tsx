import { ErrorAlert } from '@/components/error-alert';
import {
  lazy,
  Suspense,
  useCallback,
  useId,
  useMemo,
  useRef,
  useState,
} from 'react';
import { LoaderCircle } from 'lucide-react';
import type { QAPreview } from '@/api/queries';
import { useAssignmentPdf } from '@/hooks/use-assignment-pdf';
import { useScrollableOutline } from '@/hooks/use-scrollable-outline';
import {
  previewOutlineGroups,
  questionOutlineEntries,
  questionOutlineTargetId,
  type OutlineEntry,
} from '@/lib/assignment-outline';
import { QAPreviewView } from './qa-preview';
import { QAOutlineRail } from './qa-outline';
import { PdfActionButton } from './pdf-action-button';
import { Button } from './ui/button';
import { Label } from './ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './ui/select';
import { Tabs, TabsList, TabsTrigger, TabsContent } from './ui/tabs';

// Lazy load since its not needed on render.
const AssignmentPdfViewer = lazy(() => import('./assignment-pdf-viewer'));

type Props = {
  preview?: QAPreview;
  blocked?: boolean;
} & (
  | {
      source: string;
      current: boolean;
      assignmentId?: never;
      showGuide?: boolean;
      savedAssignment?: { id: string; source: string };
    }
  | {
      assignmentId: string;
      source?: never;
      current?: never;
      showGuide?: never;
      savedAssignment?: never;
    }
);

export function AssignmentPreview(props: Props) {
  const { preview, blocked = false } = props;
  const [view, setView] = useState('pdf');
  const [selectedVersion, setSelectedVersion] = useState('');
  const [activeGuideKey, setActiveGuideKey] = useState<string | null>(null);
  const guideRef = useRef<HTMLDivElement>(null);
  const versions = preview?.document.versions?.length
    ? preview.document.versions
    : ['A'];
  const version = versions.includes(selectedVersion)
    ? selectedVersion
    : versions[0];
  const valid =
    !!preview && (props.assignmentId !== undefined || props.current === true);
  const showGuide =
    props.assignmentId !== undefined || props.showGuide === true;
  const savedPdfAssignmentId =
    props.assignmentId ??
    (props.savedAssignment?.source === props.source
      ? props.savedAssignment.id
      : undefined);
  const { pdf, url, update, busy, current, error } = useAssignmentPdf(
    props.source ?? props.assignmentId,
    version,
    savedPdfAssignmentId
  );
  const currentPdf = valid && current;
  const guideVersion = preview?.document.versions?.length ? version : '';
  const guideEntries = useMemo(
    () =>
      preview
        ? questionOutlineEntries(previewOutlineGroups(preview.document)).filter(
            (entry) => entry.version === guideVersion
          )
        : [],
    [preview, guideVersion]
  );
  const currentGuideKey = guideEntries.some(
    (entry) => entry.key === activeGuideKey
  )
    ? activeGuideKey
    : (guideEntries[0]?.key ?? null);
  const guideTargetId = useCallback(
    (entry: OutlineEntry) => questionOutlineTargetId(entry.key),
    []
  );
  const navigateToGuideQuestion = useCallback((key: string) => {
    const container = guideRef.current;
    const target = document.getElementById(questionOutlineTargetId(key));
    if (!container || !target) return;
    const top =
      container.scrollTop +
      target.getBoundingClientRect().top -
      container.getBoundingClientRect().top;
    container.scrollTo({ top, behavior: 'instant' });
    target.focus({ preventScroll: true });
    setActiveGuideKey(key);
  }, []);
  useScrollableOutline(
    guideRef,
    guideEntries,
    guideTargetId,
    view !== 'guide' || !valid,
    setActiveGuideKey
  );
  const versionId = useId();
  return (
    <section
      className="@container min-w-0 space-y-4"
      aria-label="Assignment preview"
    >
      <Tabs value={view} onValueChange={(value) => setView(String(value))}>
        <div className="flex flex-wrap items-end gap-x-4 gap-y-3">
          {showGuide ? (
            <TabsList aria-label="Preview view">
              <TabsTrigger value="pdf">PDF preview</TabsTrigger>
              <TabsTrigger value="guide">Grading guide</TabsTrigger>
            </TabsList>
          ) : (
            <h2 className="text-lg font-semibold">PDF preview</h2>
          )}
          {versions.length > 1 && (
            <div className="ms-auto flex shrink-0 items-center gap-2 pb-1 @max-[24rem]:ms-0 @max-[24rem]:w-full @max-[24rem]:flex-col @max-[24rem]:items-start @max-[24rem]:justify-start">
              <Label htmlFor={versionId}>Version</Label>
              <Select
                value={version}
                disabled={blocked || !valid}
                onValueChange={(value) => {
                  if (value) {
                    setSelectedVersion(value);
                    guideRef.current?.scrollTo({ top: 0, behavior: 'instant' });
                    setActiveGuideKey(null);
                  }
                }}
              >
                <SelectTrigger id={versionId} className="max-w-full min-w-20">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent align="end" alignItemWithTrigger={false}>
                  {versions.map((item) => (
                    <SelectItem key={item} value={item}>
                      {item}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
        </div>
        <TabsContent
          value="pdf"
          keepMounted
          className="space-y-4 data-hidden:hidden"
        >
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="h-8 text-xs"
                disabled={!valid || blocked || busy}
                onClick={() => void update()}
              >
                {busy && (
                  <LoaderCircle
                    className="motion-safe:animate-spin"
                    aria-hidden="true"
                  />
                )}
                Update PDF
              </Button>
              <PdfActionButton
                url={currentPdf ? url : undefined}
                filename={`assignment-${version.replace(/[^a-zA-Z0-9_-]/g, '_')}.pdf`}
              />
            </div>
            <p
              role="status"
              className="ms-auto max-w-full min-w-0 text-end text-xs break-words text-muted-foreground @max-[24rem]:ms-0 @max-[24rem]:w-full @max-[24rem]:text-start"
            >
              {busy
                ? 'Generating…'
                : pdf && !currentPdf
                  ? `Outdated · Version ${pdf.version}`
                  : !valid
                    ? 'Valid QA required'
                    : !pdf
                      ? 'Not generated'
                      : 'Up to date'}
            </p>
          </div>
          {error && <ErrorAlert error={error} />}
          {!!preview?.warnings.length && (
            <p className="border-s-2 ps-3">
              Referenced images are not included in the generated PDF.
            </p>
          )}
          {pdf && url && (
            <Suspense fallback={<p role="status">Loading PDF viewer…</p>}>
              <AssignmentPdfViewer key={pdf.version} blob={pdf.blob} />
            </Suspense>
          )}
        </TabsContent>
        {showGuide && (
          <TabsContent
            value="guide"
            ref={guideRef}
            id="grading-guide"
            className="custom-scrollbar max-h-[70svh] overflow-y-auto pe-2"
          >
            {valid && preview ? (
              <div
                className={
                  guideEntries.length > 1
                    ? 'space-y-4 min-[65rem]:grid min-[65rem]:grid-cols-[2.25rem_minmax(0,1fr)] min-[65rem]:gap-4 min-[65rem]:space-y-0'
                    : undefined
                }
              >
                {guideEntries.length > 1 && (
                  <div className="sticky top-0 z-20 flex justify-end bg-background min-[65rem]:top-1/2 min-[65rem]:block min-[65rem]:-translate-y-1/2 min-[65rem]:self-start">
                    <QAOutlineRail
                      entries={guideEntries}
                      activeKey={currentGuideKey}
                      outdated={!valid}
                      disabled={blocked}
                      editorId="grading-guide"
                      showVersionHeadings
                      onVersionSelect={() => {
                        guideRef.current?.scrollTo({
                          top: 0,
                          behavior: 'instant',
                        });
                        setActiveGuideKey(guideEntries[0]?.key ?? null);
                      }}
                      canSelect={() => true}
                      onSelect={(entry) => navigateToGuideQuestion(entry.key)}
                    />
                  </div>
                )}
                <QAPreviewView
                  preview={preview}
                  versionOverride={guideVersion}
                  onQuestionSelect={navigateToGuideQuestion}
                />
              </div>
            ) : (
              <p className="py-4 text-muted-foreground">
                The grading guide will update when the QA markdown is valid.
              </p>
            )}
          </TabsContent>
        )}
      </Tabs>
    </section>
  );
}
