import {
  memo,
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type Ref,
  type RefObject,
} from 'react';
import {
  questionOutlineEntries,
  questionOutlineKey,
  type OutlineEntry,
} from '@/lib/assignment-outline';
import {
  editAssignmentDraft,
  type QAPreview,
  type DocumentEdit,
  type QuestionEdit,
  type VisualEditResult,
} from '@/api/queries';
import { useOutlineTracking } from '@/hooks/use-outline-tracking';
import { schedulePreview } from '@/lib/debounced-preview';
import { errorMessage } from '@/lib/error-message';
import {
  validationQuestionTarget,
  type ValidationLocation,
} from '@/lib/validation-source-line';
import { VersionBar } from './version-editor';
import {
  SortableQuestionList,
  type QuestionListNavigation,
} from './sortable-question-list';
import { VisualQuestionEditor } from './visual-question-editor';
import { Plus } from 'lucide-react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Textarea } from './ui/textarea';

type Question = QAPreview['document']['questions'][number];

const questionTargetId = (entry: OutlineEntry) =>
  `visual-question-${entry.line}`;

function questionDraft(question: Question, version: string): QuestionEdit {
  return {
    source_line: question.source_line!,
    prompt: question.prompt,
    points: question.points,
    answers: question.answers ?? [],
    rubric: question.rubric ?? [],
    choices: question.choices ?? [],
    correct_choice_labels: question.correct_choice_labels?.length
      ? question.correct_choice_labels
      : question.correct_choice_label
        ? [question.correct_choice_label]
        : [],
    version: version || null,
    remove: false,
  };
}

export type VisualEditorNavigation = {
  navigate: (entry: OutlineEntry) => void;
  navigateError: (location: ValidationLocation) => boolean;
  addQuestion: () => void;
  retry: () => void;
};

export const VisualAssignmentEditor = memo(function VisualAssignmentEditor({
  ref,
  onOutlineChange,
  onActiveChange,
  source,
  preview,
  disabled,
  structureLocked,
  scrollElementRef,
  onPending,
  onResult,
  onFailure,
  errorKey,
  validationError,
}: {
  ref?: Ref<VisualEditorNavigation>;
  onOutlineChange: (entries: OutlineEntry[], version: string) => void;
  onActiveChange: (key: string) => void;
  source: string;
  preview: QAPreview;
  disabled: boolean;
  structureLocked: boolean;
  scrollElementRef?: RefObject<HTMLElement | null>;
  onPending: () => void;
  onResult: (result: VisualEditResult) => void;
  onFailure: (message: string) => void;
  errorKey?: string;
  validationError?: string;
}) {
  // All patches share this baseline until the user switches representation.
  const [baseline] = useState(() => ({ source, preview }));
  const [edits, setEdits] = useState<Record<number, QuestionEdit>>({});
  const [added, setAdded] = useState<Record<string, Question[]>>({});
  const nextQuestion = useRef(-1);
  const [metadata, setMetadata] = useState({
    title: preview.document.title ?? '',
    instructions: preview.document.instructions ?? '',
    question_orders: [] as NonNullable<DocumentEdit['question_orders']>,
    versions: (preview.document.versions?.length
      ? preview.document.versions
      : ['']
    ).map((label) => ({
      key: label,
      original_label: label as string | null,
      label,
    })),
  } satisfies DocumentEdit);
  const [retry, setRetry] = useState(0);
  const revision = useRef(0);
  const versions = metadata.versions;
  const [version, setVersion] = useState(versions[0].key);
  const [draggingParent, setDraggingParent] = useState<number | null>();
  const questionListRef = useRef<QuestionListNavigation>(null);
  const editorHeaderRef = useRef<HTMLDivElement>(null);
  const versionBarRef = useRef<HTMLDivElement>(null);
  const compact = draggingParent !== undefined;

  useEffect(() => {
    if (revision.current === 0) return;
    const currentRevision = revision.current;
    return schedulePreview(
      (signal) =>
        editAssignmentDraft(
          baseline.source,
          Object.values(edits),
          metadata,
          signal
        ),
      (result) => {
        if (revision.current !== currentRevision) return;
        if ('data' in result) onResult(result.data);
        else onFailure(errorMessage(result.error));
      }
    );
  }, [baseline.source, edits, metadata, retry, onResult, onFailure]);

  function change(edit: QuestionEdit) {
    revision.current += 1;
    onPending();
    setEdits((previous) => ({ ...previous, [edit.source_line]: edit }));
  }

  function addQuestion() {
    const line = nextQuestion.current--;
    const question: Question = {
      id: `new-${-line}`,
      source_line: line,
      prompt: 'New question',
      points: 10,
      answers: [],
      rubric: [],
      correct_choice_labels: [],
      outcomes: [],
    };
    setAdded((previous) => ({
      ...previous,
      [version]: [...(previous[version] ?? []), question],
    }));
    change(questionDraft(question, version));
    setNavigation({ version, line });
  }

  const groups = useMemo(() => {
    function orderedQuestions(
      questions: Question[],
      version: string,
      parent: number | null = null
    ): Question[] {
      const order =
        metadata.question_orders.find(
          (order) =>
            order.version === version && order.parent_source_line === parent
        )?.source_lines ?? [];
      const positions = new Map(order.map((line, index) => [line, index]));
      return questions
        .filter((question) => !edits[question.source_line!]?.remove)
        .toSorted(
          (a, b) =>
            (positions.get(a.source_line!) ?? order.length) -
            (positions.get(b.source_line!) ?? order.length)
        )
        .map((question) => ({
          ...question,
          prompt: edits[question.source_line!]?.prompt ?? question.prompt,
          points: edits[question.source_line!]?.points ?? question.points,
          choices: edits[question.source_line!]?.choices ?? question.choices,
          correct_choice_labels:
            edits[question.source_line!]?.correct_choice_labels ??
            question.correct_choice_labels,
          subquestions: orderedQuestions(
            question.subquestions ?? [],
            version,
            question.source_line!
          ),
        }));
    }
    return versions.map(({ key, label, original_label }) => ({
      key,
      label,
      questions: orderedQuestions(
        [
          ...(original_label === null
            ? []
            : original_label
              ? (baseline.preview.document.version_questions?.[
                  original_label
                ] ?? [])
              : baseline.preview.document.questions),
          ...(added[key] ?? []),
        ],
        key
      ),
    }));
  }, [versions, metadata.question_orders, edits, baseline.preview, added]);
  const entries = useMemo(() => questionOutlineEntries(groups), [groups]);
  useEffect(
    () => onOutlineChange(entries, version),
    [entries, version, onOutlineChange]
  );

  const versionEntries = useMemo(
    () => entries.filter((entry) => entry.version === version),
    [entries, version]
  );
  useOutlineTracking({
    entries: versionEntries,
    targetId: questionTargetId,
    paused: compact || !!scrollElementRef,
    onActiveChange,
  });

  const activeEntryKeys = useMemo(
    () =>
      new Map(
        versionEntries
          .filter((entry) => entry.line !== null)
          .map((entry) => [entry.line, entry.key])
      ),
    [versionEntries]
  );
  const updateVisibleQuestion = useCallback(
    (sourceLine: number) => {
      const key = activeEntryKeys.get(sourceLine);
      if (key) onActiveChange(key);
    },
    [activeEntryKeys, onActiveChange]
  );

  const [navigation, setNavigation] = useState<{
    version: string;
    line: number | null;
    rubricPath?: number[] | null;
  } | null>(null);
  useImperativeHandle(
    ref,
    () => ({
      navigate(entry) {
        setVersion(entry.version);
        setNavigation({ ...entry });
      },
      navigateError(location) {
        const target = validationQuestionTarget(location, groups);
        if (!target) return false;
        setVersion(target.version);
        setNavigation(target);
        return true;
      },
      addQuestion,
      retry() {
        onPending();
        setRetry((value) => value + 1);
      },
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [groups, version]
  );
  useLayoutEffect(() => {
    if (!navigation || navigation.line === null) return;
    const targetLine = navigation.line;
    let firstFrame = 0;
    let secondFrame = 0;
    function focusTarget() {
      let target = document.getElementById(`visual-question-${targetLine}`);
      if (!target) return false;
      for (const index of navigation?.rubricPath ?? []) {
        const item: HTMLElement | null = target.querySelector(
          `[data-rubric-list] > [data-rubric-index="${index}"]`
        );
        if (!item) break;
        target = item;
      }
      questionListRef.current?.scrollToElement(target);
      const field = navigation?.rubricPath
        ? (target.querySelector<HTMLInputElement>(
            'input[type="number"]:not(:disabled)'
          ) ??
          target.querySelector<HTMLTextAreaElement>('textarea:not(:disabled)'))
        : target.querySelector<HTMLTextAreaElement>('textarea:not(:disabled)');
      field?.focus({ preventScroll: true });
      setNavigation(null);
      return true;
    }
    firstFrame = requestAnimationFrame(() => {
      if (focusTarget()) return;
      questionListRef.current?.scrollTo(targetLine);
      secondFrame = requestAnimationFrame(() => focusTarget());
    });
    return () => {
      cancelAnimationFrame(firstFrame);
      cancelAnimationFrame(secondFrame);
    };
  }, [navigation]);

  const activeGroup = groups.find((group) => group.key === version);
  const questionCounts = useMemo(
    () =>
      Object.fromEntries(
        groups.map((group) => [group.key, group.questions.length])
      ),
    [groups]
  );

  function renderQuestions(
    questions: Question[],
    version: string,
    parent: number | null = null,
    prefix = ''
  ) {
    return (
      <SortableQuestionList
        ref={parent === null ? questionListRef : undefined}
        questions={questions}
        disabled={disabled || structureLocked}
        compact={compact}
        scrollElementRef={parent === null ? scrollElementRef : undefined}
        offsetElementRef={parent === null ? editorHeaderRef : undefined}
        stickyElementRef={parent === null ? versionBarRef : undefined}
        onVisibleQuestionChange={
          parent === null ? updateVisibleQuestion : undefined
        }
        labelPrefix={prefix}
        onDraggingChange={(dragging) =>
          setDraggingParent(dragging ? parent : undefined)
        }
        onMove={(source_lines) => {
          revision.current += 1;
          onPending();
          setMetadata({
            ...metadata,
            question_orders: [
              ...metadata.question_orders.filter(
                (order) =>
                  order.version !== version ||
                  order.parent_source_line !== parent
              ),
              { version, parent_source_line: parent, source_lines },
            ],
          });
        }}
        renderQuestion={(question, index, dragHandle) => {
          const edit =
            edits[question.source_line!] ?? questionDraft(question, version);
          const number = `${prefix}${index + 1}`;
          const label = `Question ${number}`;
          const outlineKey = questionOutlineKey(version, question);
          return (
            <div
              onFocusCapture={(event) => {
                if (
                  event.target.closest('section')?.id ===
                  `visual-question-${question.source_line}`
                )
                  onActiveChange(outlineKey);
              }}
            >
              <VisualQuestionEditor
                edit={edit}
                label={label}
                error={outlineKey === errorKey ? validationError : undefined}
                parts={
                  question.subquestions?.length ? (
                    <div
                      hidden={
                        compact && draggingParent !== question.source_line
                      }
                      className={compact ? 'pt-2' : undefined}
                    >
                      {renderQuestions(
                        question.subquestions,
                        version,
                        question.source_line!,
                        `${number}.`
                      )}
                    </div>
                  ) : undefined
                }
                dragHandle={dragHandle}
                compact={compact}
                deferOffscreen={!scrollElementRef && parent === null}
                disabled={disabled}
                onChange={change}
                onRemove={
                  structureLocked
                    ? undefined
                    : () => change({ ...edit, remove: true })
                }
              />
            </div>
          );
        }}
      />
    );
  }

  return (
    <div id="visual-editor" className="space-y-6">
      <div ref={editorHeaderRef} className="space-y-6">
        <Label className="block space-y-2 text-sm leading-normal">
          <span>Title</span>
          <Input
            value={metadata.title}
            disabled={disabled}
            onChange={(event) => {
              revision.current += 1;
              onPending();
              setMetadata({ ...metadata, title: event.target.value });
            }}
          />
        </Label>
        <Label className="block space-y-2 text-sm leading-normal">
          <span>Instructions</span>
          <Textarea
            value={metadata.instructions}
            disabled={disabled}
            onChange={(event) => {
              revision.current += 1;
              onPending();
              setMetadata({ ...metadata, instructions: event.target.value });
            }}
          />
        </Label>
      </div>
      <VersionBar
        ref={versionBarRef}
        versions={versions}
        questionCounts={questionCounts}
        selected={version}
        disabled={disabled}
        structureLocked={structureLocked}
        onSelect={setVersion}
        onChange={(versions) => {
          revision.current += 1;
          onPending();
          setMetadata({ ...metadata, versions });
        }}
      />
      {activeGroup &&
        (activeGroup.questions.length ? (
          <div className="space-y-4">
            {renderQuestions(activeGroup.questions, activeGroup.key)}
            {!structureLocked && (
              <Button
                type="button"
                variant="outline"
                className="w-full border-dashed"
                disabled={disabled}
                onClick={addQuestion}
              >
                <Plus aria-hidden="true" />
                Add question
              </Button>
            )}
          </div>
        ) : (
          <div className="flex flex-col items-center gap-3 border border-dashed px-4 py-10 text-center text-sm text-muted-foreground">
            <p>
              {activeGroup.label
                ? `Version ${activeGroup.label} has no questions yet.`
                : 'This answer key has no questions yet.'}
            </p>
            {!structureLocked && (
              <Button
                type="button"
                variant="outline"
                disabled={disabled}
                onClick={addQuestion}
              >
                <Plus aria-hidden="true" />
                Add question
              </Button>
            )}
          </div>
        ))}
    </div>
  );
});
