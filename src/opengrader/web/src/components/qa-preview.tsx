import { useId, useState } from 'react';
import type { components } from '@/api/schema';
import type { QAPreview } from '@/api/queries';
import { Label } from './ui/label';
import { GradingText } from './grading-text';

type Question = components['schemas']['QAQuestion'];
type Subquestion = components['schemas']['QASubquestion'];
type RubricItem = components['schemas']['QARubricItem'];

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

function QuestionContent({ question }: { question: Question | Subquestion }) {
  const correct = question.correct_choice_labels?.length
    ? question.correct_choice_labels
    : question.correct_choice_label
      ? [question.correct_choice_label]
      : [];
  return (
    <div className="min-w-0 space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="font-semibold">Question {question.id}</p>
        <span className="tabular-nums">{question.points} points</span>
      </div>
      <GradingText>{question.prompt}</GradingText>
      {!!question.choices?.length && (
        <ul className="space-y-2">
          {question.choices.map((choice) => (
            <li key={choice.label} className="flex flex-wrap gap-2">
              <span>{choice.label}.</span>
              <GradingText>{choice.text}</GradingText>
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
      {!!question.rubric?.length && (
        <div className="space-y-3">
          <p className="font-medium">Rubric</p>
          <RubricTree items={question.rubric} />
        </div>
      )}
      {!!question.outcomes?.length && (
        <p>Outcomes: {question.outcomes.join(', ')}</p>
      )}
      {'subquestions' in question && !!question.subquestions?.length && (
        <div className="space-y-6 border-s ps-4">
          {question.subquestions.map((subquestion) => (
            <QuestionContent key={subquestion.id} question={subquestion} />
          ))}
        </div>
      )}
    </div>
  );
}

export function QAPreviewView({
  preview,
  versionOverride,
}: {
  preview: QAPreview;
  versionOverride?: string;
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
    <div className="min-w-0 space-y-6 break-words">
      {warnings.map((warning) => (
        <p key={warning} className="border-s-2 ps-3">
          {warning}
        </p>
      ))}
      <div className="space-y-3">
        <h2 className="text-xl font-semibold">
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
            <QuestionContent question={question} />
          </li>
        ))}
      </ol>
    </div>
  );
}
