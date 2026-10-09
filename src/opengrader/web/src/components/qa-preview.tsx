import { useId, useState } from 'react';
import type { components } from '@/api/schema';
import type { QAPreview } from '@/api/queries';
import {
  questionOutlineKey,
  questionOutlineTargetId,
} from '@/lib/assignment-outline';
import { PenLine } from 'lucide-react';
import { Button } from './ui/button';
import { Label } from './ui/label';
import { GradingText } from './grading-text';

type Question = components['schemas']['QAQuestion'];
type Subquestion = components['schemas']['QASubquestion'];
type RubricItem = NonNullable<Question['rubric']>[number];

function RubricTree({ items }: { items: RubricItem[] }) {
  return (
    <ul className="space-y-3 border-s ps-4">
      {items.map((item, index) => (
        <li key={index} className="space-y-2">
          <div className="flex flex-wrap items-baseline gap-x-3">
            <GradingText>{item.label}</GradingText>
            {item.points_delta != null && (
              <span className="tabular-nums">
                {item.points_delta > 0 ? '+' : ''}
                {item.points_delta} points
              </span>
            )}
          </div>
          {item.notes?.map((note, i) => (
            <GradingText key={i}>{note}</GradingText>
          ))}
          {item.default_comment && (
            <div className="space-y-1">
              <p className="text-sm font-medium">Feedback</p>
              <GradingText>{item.default_comment}</GradingText>
            </div>
          )}
          {!!item.subitems?.length && <RubricTree items={item.subitems} />}
        </li>
      ))}
    </ul>
  );
}

function QuestionContent({
  question,
  version,
  depth,
  onQuestionSelect,
  onEditRubric,
}: {
  question: Question | Subquestion;
  version: string;
  depth: number;
  onQuestionSelect: (key: string) => void;
  onEditRubric?: (questionId: string) => void;
}) {
  const outlineKey = questionOutlineKey(version, question);
  // A question with parts is graded by its parts, which have the rubrics.
  const graded = !('subquestions' in question && question.subquestions?.length);
  const editRubric = graded && onEditRubric;
  const questionLabel = `Question ${question.id}`;
  const pointsLabel = `${question.points} points`;
  const correct = question.correct_choice_labels?.length
    ? question.correct_choice_labels
    : question.correct_choice_label
      ? [question.correct_choice_label]
      : [];
  return (
    <div
      id={questionOutlineTargetId(outlineKey)}
      tabIndex={-1}
      className="min-w-0 space-y-4 focus-visible:outline-2 focus-visible:-outline-offset-2"
    >
      <div
        className="sticky top-0 flex min-h-11 min-w-0 items-center gap-3 overflow-hidden border-b bg-background px-2"
        style={{
          zIndex: 10 + depth,
          marginInlineStart: `calc(-0.5rem - ${depth}rem)`,
          width: `calc(100% + 1rem + ${depth}rem)`,
        }}
      >
        <button
          type="button"
          aria-label={`Scroll to start of ${questionLabel}`}
          title={`${questionLabel}. Scroll to question start.`}
          className="flex min-h-11 min-w-0 flex-1 items-center text-start font-semibold underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:-outline-offset-2"
          onClick={() => onQuestionSelect(outlineKey)}
        >
          <span className="block min-w-0 truncate">{questionLabel}</span>
        </button>
        <span
          className="max-w-[40%] shrink-0 truncate text-end tabular-nums"
          title={pointsLabel}
        >
          {pointsLabel}
        </span>
      </div>
      <GradingText>{question.prompt}</GradingText>
      {!!question.choices?.length && (
        <ul className="space-y-2">
          {question.choices.map((choice) => (
            <li
              key={choice.label}
              className="flex flex-wrap items-baseline gap-2"
            >
              <span>{choice.label}.</span>
              <div className="min-w-0 flex-1">
                <GradingText>{choice.text}</GradingText>
              </div>
              {correct.includes(choice.label) && (
                <span className="font-medium">Correct answer</span>
              )}
            </li>
          ))}
        </ul>
      )}
      {!!question.answers?.length && (
        <div className="space-y-2">
          <p className="font-medium">Expected answers</p>
          {question.answers.map((answer, index) => (
            <GradingText key={index}>{answer}</GradingText>
          ))}
        </div>
      )}
      {question.image_markdown && (
        <div className="space-y-1 text-muted-foreground">
          <p>Image unavailable</p>
          <pre className="text-sm break-all whitespace-pre-wrap">
            {question.image_markdown}
          </pre>
        </div>
      )}
      {(!!question.rubric?.length || editRubric) && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <p className="font-medium">Rubric</p>
            {editRubric && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                aria-label={`Edit the rubric of question ${question.id}`}
                onClick={() => editRubric(question.id)}
              >
                <PenLine aria-hidden="true" />
                Edit rubric
              </Button>
            )}
          </div>
          {question.rubric?.length ? (
            <RubricTree items={question.rubric} />
          ) : (
            <p className="text-muted-foreground">
              None yet. Graders choose Correct or Incorrect, or enter a score.
            </p>
          )}
        </div>
      )}
      {!!question.outcomes?.length && (
        <p>Outcomes: {question.outcomes.join(', ')}</p>
      )}
      {'subquestions' in question && !!question.subquestions?.length && (
        <div className="space-y-6 border-s ps-4">
          {question.subquestions.map((subquestion) => (
            <QuestionContent
              key={subquestion.id}
              question={subquestion}
              version={version}
              depth={depth + 1}
              onQuestionSelect={onQuestionSelect}
              onEditRubric={onEditRubric}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export function QAPreviewView({
  preview,
  versionOverride,
  onQuestionSelect,
  onEditRubric,
}: {
  preview: QAPreview;
  versionOverride?: string;
  onQuestionSelect: (key: string) => void;
  /** Given the id of a question in the shown version; omit to show no button. */
  onEditRubric?: (questionId: string) => void;
}) {
  const { document, warnings } = preview;
  const [selectedVersion, setSelectedVersion] = useState('');
  const version =
    versionOverride ??
    (document.versions?.includes(selectedVersion)
      ? selectedVersion
      : document.versions?.[0]);
  const questions =
    version && document.versions?.length
      ? (document.version_questions?.[version] ?? [])
      : document.questions;
  const versionId = useId();
  return (
    <div className="min-w-0 space-y-6 wrap-break-word">
      {warnings.map((warning) => (
        <p key={warning} className="border-s-2 ps-3">
          {warning}
        </p>
      ))}
      <div className="space-y-3">
        <h2
          className="truncate text-xl font-semibold"
          title={document.title || 'Untitled assignment'}
        >
          {document.title || 'Untitled assignment'}
        </h2>
        {document.instructions && (
          <GradingText>{document.instructions}</GradingText>
        )}
      </div>
      {!versionOverride && !!document.versions?.length && (
        <div className="grid justify-items-start gap-2">
          <Label htmlFor={versionId}>Version</Label>
          <select
            id={versionId}
            value={version}
            onChange={(event) => setSelectedVersion(event.target.value)}
            className="min-h-11 max-w-full rounded-md border bg-background px-3 py-2 text-base"
          >
            {document.versions.map((label) => (
              <option key={label}>{label}</option>
            ))}
          </select>
        </div>
      )}
      <ol className="space-y-8">
        {questions.map((question) => (
          <li key={question.id} className="border-t pt-6">
            <QuestionContent
              question={question}
              version={version ?? ''}
              depth={0}
              onQuestionSelect={onQuestionSelect}
              onEditRubric={onEditRubric}
            />
          </li>
        ))}
      </ol>
    </div>
  );
}
