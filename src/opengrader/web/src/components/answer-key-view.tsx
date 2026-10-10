import {
  memo,
  useCallback,
  useEffect,
  useId,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type Ref,
  type RefObject,
} from 'react';
import { useQuery } from '@tanstack/react-query';
import { PenLine } from 'lucide-react';
import { answerFieldsQuery, type QAPreview } from '@/api/queries';
import { useOutlineTracking } from '@/hooks/use-outline-tracking';
import {
  previewOutlineGroups,
  questionOutlineEntries,
  questionOutlineTargetId,
  type OutlineEntry,
} from '@/lib/assignment-outline';
import type { VisualEditorNavigation } from './visual-assignment-editor';
import { unanswered } from '@/lib/answers';
import { AnswersSheet } from './answers-sheet';
import { QAPreviewView } from './qa-preview';
import { RubricEditSheet, type RubricTarget } from './rubric-edit-sheet';
import { Button } from './ui/button';
import { Label } from './ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './ui/select';

const targetId = (entry: OutlineEntry) => questionOutlineTargetId(entry.key);

export const AnswerKeyView = memo(function AnswerKeyView({
  ref,
  preview,
  active,
  scrollElementRef,
  onOutlineChange,
  onActiveChange,
  rubricEditAssignmentId,
}: {
  ref?: Ref<VisualEditorNavigation>;
  preview: QAPreview;
  active: boolean;
  scrollElementRef: RefObject<HTMLElement | null>;
  onOutlineChange: (entries: OutlineEntry[], version: string) => void;
  onActiveChange: (key: string) => void;
  /** Set where a rubric can be edited in place (a folder session); else read-only. */
  rubricEditAssignmentId?: string;
}) {
  const versions = preview.document.versions ?? [];
  const [version, setVersion] = useState(versions[0] ?? '');
  const [rubricTarget, setRubricTarget] = useState<RubricTarget | null>(null);
  const [addingAnswers, setAddingAnswers] = useState(false);
  // Only where answers can be saved (a folder session), and only to know
  // whether any are missing: the button is not shown otherwise.
  const answerFields = useQuery({
    ...answerFieldsQuery(rubricEditAssignmentId ?? ''),
    enabled: Boolean(rubricEditAssignmentId),
  });
  const answersLeft = unanswered(answerFields.data, version || null).length;
  const pendingTarget = useRef<string | null>(null);
  const entries = useMemo(
    () => questionOutlineEntries(previewOutlineGroups(preview.document)),
    [preview]
  );
  const versionEntries = useMemo(
    () => entries.filter((entry) => entry.version === version),
    [entries, version]
  );
  useEffect(
    () => onOutlineChange(entries, version),
    [entries, version, onOutlineChange]
  );
  useOutlineTracking({
    container: scrollElementRef,
    entries: versionEntries,
    targetId,
    paused: !active,
    onActiveChange,
  });
  const scrollToQuestion = useCallback(
    (key: string) => {
      const container = scrollElementRef.current;
      const element = document.getElementById(questionOutlineTargetId(key));
      if (!container || !element) return;
      container.scrollTo({
        top:
          container.scrollTop +
          element.getBoundingClientRect().top -
          container.getBoundingClientRect().top,
        behavior: 'instant',
      });
      element.focus({ preventScroll: true });
    },
    [scrollElementRef]
  );
  useImperativeHandle(
    ref,
    () => ({
      navigate(entry) {
        if (entry.version === version) scrollToQuestion(entry.key);
        else {
          pendingTarget.current = entry.key;
          setVersion(entry.version);
        }
      },
      navigateError: () => false,
      addQuestion: () => undefined,
      retry: () => undefined,
    }),
    [version, scrollToQuestion]
  );
  useLayoutEffect(() => {
    if (!pendingTarget.current) return;
    scrollToQuestion(pendingTarget.current);
    pendingTarget.current = null;
  }, [version, scrollToQuestion]);
  const versionId = useId();

  return (
    <div className="space-y-6 px-2 pt-2">
      {versions.length > 1 && (
        <div className="flex items-center gap-3">
          <Label htmlFor={versionId}>Version</Label>
          <Select
            value={version}
            onValueChange={(value) => {
              if (value) {
                setVersion(value);
                scrollElementRef.current?.scrollTo({
                  top: 0,
                  behavior: 'instant',
                });
              }
            }}
          >
            <SelectTrigger id={versionId} className="min-w-20">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {versions.map((item) => (
                <SelectItem key={item} value={item}>
                  {item}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}
      {rubricEditAssignmentId && answersLeft > 0 && (
        <div className="flex flex-wrap items-center gap-3 border border-dashed p-3">
          <p className="min-w-0 flex-1 text-sm">
            {answersLeft === 1
              ? '1 question has no answer yet.'
              : `${answersLeft} questions have no answer yet.`}{' '}
            Without one, a question is graded by hand.
          </p>
          <Button onClick={() => setAddingAnswers(true)}>
            <PenLine aria-hidden="true" />
            Add answers
          </Button>
        </div>
      )}
      <QAPreviewView
        preview={preview}
        versionOverride={version}
        onQuestionSelect={scrollToQuestion}
        onEditRubric={
          rubricEditAssignmentId
            ? (questionId) =>
                setRubricTarget({ questionId, version: version || null })
            : undefined
        }
      />
      {rubricEditAssignmentId && (
        <RubricEditSheet
          assignmentId={rubricEditAssignmentId}
          target={rubricTarget}
          onClose={() => setRubricTarget(null)}
        />
      )}
      {rubricEditAssignmentId && (
        <AnswersSheet
          assignmentId={rubricEditAssignmentId}
          version={version || null}
          open={addingAnswers}
          onClose={() => setAddingAnswers(false)}
        />
      )}
    </div>
  );
});
