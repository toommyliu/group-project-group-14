import { ErrorAlert } from '@/components/error-alert';
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
import { VersionEditor } from './version-editor';
import {
  SortableQuestionList,
  type QuestionListNavigation,
} from './sortable-question-list';
import { VisualQuestionEditor } from './visual-question-editor';
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
};

export const VisualAssignmentEditor = memo(function VisualAssignmentEditor({
  ref,
  onOutlineChange,
  onActiveChange,
  source,
  preview,
  disabled,
  scrollElementRef,
  onPending,
  onResult,
}: {
  ref?: Ref<VisualEditorNavigation>;
  onOutlineChange: (entries: OutlineEntry[], version: string) => void;
  onActiveChange: (key: string) => void;
  source: string;
  preview: QAPreview;
  disabled: boolean;
  scrollElementRef?: RefObject<HTMLElement | null>;
  onPending: () => void;
  onResult: (result: VisualEditResult) => void;
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
  const [failure, setFailure] = useState('');
  const revision = useRef(0);
  const versions = metadata.versions;
  const [version, setVersion] = useState(versions[0].key);
  const [draggingParent, setDraggingParent] = useState<number | null>();
  const questionListRef = useRef<QuestionListNavigation>(null);
  const editorHeaderRef = useRef<HTMLDivElement>(null);
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
        if ('data' in result) {
          setFailure('');
          onResult(result.data);
        } else setFailure(errorMessage(result.error));
      }
    );
  }, [baseline.source, edits, metadata, retry, onResult]);

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
    }),
    [groups]
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
        disabled={disabled}
        compact={compact}
        scrollElementRef={parent === null ? scrollElementRef : undefined}
        offsetElementRef={parent === null ? editorHeaderRef : undefined}
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
          return (
            <div
              className={`flex flex-col ${compact ? 'gap-2' : 'gap-6'}`}
              onFocusCapture={(event) => {
                if (
                  event.target.closest('section')?.id ===
                  `visual-question-${question.source_line}`
                )
                  onActiveChange(
                    JSON.stringify([
                      version,
                      question.source_line ?? question.id,
                    ])
                  );
              }}
            >
              <VisualQuestionEditor
                edit={edit}
                label={`Question ${number}`}
                hasSubquestions={!!question.subquestions?.length}
                dragHandle={dragHandle}
                compact={compact}
                deferOffscreen={!scrollElementRef}
                disabled={disabled}
                onChange={change}
                onRemove={() => change({ ...edit, remove: true })}
              />
              {!!question.subquestions?.length && (
                <div
                  className="ps-4"
                  hidden={compact && draggingParent !== question.source_line}
                >
                  {renderQuestions(
                    question.subquestions,
                    version,
                    question.source_line!,
                    `${number}.`
                  )}
                </div>
              )}
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
        <VersionEditor
          versions={versions}
          selected={version}
          disabled={disabled}
          onSelect={setVersion}
          onChange={(versions) => {
            revision.current += 1;
            onPending();
            setMetadata({ ...metadata, versions });
          }}
        />
        {failure && (
          <ErrorAlert error={failure}>
            <p>Your visual edits are still here.</p>
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                onPending();
                setRetry((value) => value + 1);
              }}
            >
              Retry updating draft
            </Button>
          </ErrorAlert>
        )}
      </div>
      {activeGroup && (
        <div className="space-y-6">
          {renderQuestions(activeGroup.questions, activeGroup.key)}
        </div>
      )}
      <Button
        type="button"
        variant="outline"
        disabled={disabled}
        onClick={addQuestion}
      >
        Add question
      </Button>
    </div>
  );
});
