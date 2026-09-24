import { Fragment, useId, useState } from 'react';
import { Link } from '@tanstack/react-router';
import type {
  AssignmentVersionStatistics,
  ScoreStatistics,
} from '@/api/queries';
import { StatisticsChart } from './statistics-chart';
import { Label } from './ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './ui/select';

const number = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });
const questionLabel = (id: string) => id.replace(/^q/i, '');

function scoreValue(value: number | null, possible: number) {
  if (value === null) return 'Not graded';
  return possible > 0
    ? `${number.format((value / possible) * 100)}%`
    : `${number.format(value)} pts`;
}

export function AssignmentStatistics({
  assignmentId,
  report,
}: {
  assignmentId: string;
  report: AssignmentVersionStatistics;
}) {
  const { scores, questions } = report;
  const [distributionId, setDistributionId] = useState('total');
  const selectId = useId();
  const distribution =
    questions.find((item) => item.scores.question_id === distributionId)
      ?.scores ?? scores;
  const distributionLabel =
    distribution.question_id === 'total'
      ? 'Assignment total'
      : `Question ${questionLabel(distribution.question_id)}`;
  return (
    <div className="space-y-10">
      <section aria-label="Assignment summary" className="space-y-5">
        <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2">
          <p className="font-medium tabular-nums">
            {number.format(scores.possible_points)} points possible
          </p>
          <p className="text-muted-foreground tabular-nums">
            {scores.graded_count} of {scores.total_submissions} submissions
            fully graded
          </p>
          {report.partial_count > 0 && (
            <p className="text-muted-foreground">
              {report.partial_count} partially graded
            </p>
          )}
          {report.ungraded_count > 0 && (
            <p className="text-muted-foreground">
              {report.ungraded_count} ungraded
            </p>
          )}
        </div>
        <ScoreSummary scores={scores} />
        <p className="max-w-prose text-sm text-muted-foreground">
          Assignment totals include fully graded submissions only. Question
          statistics include graded answers only. Scores are before any curve.
        </p>
        {scores.total_submissions === 0 ? (
          <p>
            No submissions{report.version ? ' for this version' : ''} yet.{' '}
            <Link
              to="/assignments/$assignmentId/scans"
              params={{ assignmentId }}
              className="underline underline-offset-4"
            >
              Upload scans
            </Link>{' '}
            to start grading.
          </p>
        ) : scores.graded_count === 0 ? (
          <p>
            No fully graded submissions yet.{' '}
            <Link
              to="/assignments/$assignmentId/submissions"
              params={{ assignmentId }}
              search={{
                submission: questions[0]?.submission_id ?? undefined,
              }}
              className="underline underline-offset-4"
            >
              Continue grading
            </Link>{' '}
            to see assignment totals.
          </p>
        ) : null}
      </section>

      <section aria-labelledby="question-means-heading" className="space-y-4">
        <h2 id="question-means-heading" className="text-xl font-semibold">
          Mean score by question
        </h2>
        <StatisticsChart
          label="Mean score by question"
          unit="percent"
          rows={questions.map(({ scores: item }) => ({
            label: questionLabel(item.question_id),
            value: item.average_percent,
            detail: `Question ${questionLabel(item.question_id)}: ${scoreValue(item.average, item.possible_points)} mean, ${item.graded_count} of ${item.total_submissions} graded`,
          }))}
        />
        <div className="overflow-x-auto">
          <table className="w-full text-start text-sm">
            <caption className="sr-only">Question statistics</caption>
            <thead className="border-b text-muted-foreground">
              <tr>
                <th scope="col" className="py-3 pe-4 text-start font-medium">
                  Question
                </th>
                <th scope="col" className="px-3 py-3 text-end font-medium">
                  Points
                </th>
                <th scope="col" className="px-3 py-3 text-end font-medium">
                  Graded
                </th>
                <th scope="col" className="py-3 ps-3 text-end font-medium">
                  Mean
                </th>
              </tr>
            </thead>
            <tbody>
              {questions.map((item, index) => (
                <Fragment key={item.scores.question_id}>
                  {item.parent_id &&
                    questions[index - 1]?.parent_id !== item.parent_id && (
                      <tr>
                        <th
                          colSpan={4}
                          scope="row"
                          className="pt-5 pb-2 text-start font-medium"
                        >
                          {questionLabel(item.parent_id)}. {item.parent_prompt}
                        </th>
                      </tr>
                    )}
                  <tr className="border-b">
                    <th
                      scope="row"
                      className={`py-4 pe-4 text-start font-normal ${item.parent_id ? 'ps-4' : ''}`}
                    >
                      {item.submission_id !== null ? (
                        <Link
                          to="/assignments/$assignmentId/submissions"
                          params={{ assignmentId }}
                          search={{
                            submission: item.submission_id,
                            question: item.scores.question_id,
                          }}
                          className="underline underline-offset-4"
                        >
                          <span className="font-medium">
                            {questionLabel(item.scores.question_id)}.
                          </span>{' '}
                          {item.prompt}
                        </Link>
                      ) : (
                        <>
                          {questionLabel(item.scores.question_id)}.{' '}
                          {item.prompt}
                        </>
                      )}
                    </th>
                    <td className="px-3 py-4 text-end tabular-nums">
                      {number.format(item.scores.possible_points)}
                    </td>
                    <td className="px-3 py-4 text-end whitespace-nowrap tabular-nums">
                      {item.scores.graded_count} /{' '}
                      {item.scores.total_submissions}
                    </td>
                    <td className="py-4 ps-3 text-end whitespace-nowrap tabular-nums">
                      {scoreValue(
                        item.scores.average,
                        item.scores.possible_points
                      )}
                      {item.scores.average !== null &&
                        item.scores.possible_points > 0 && (
                          <span className="block text-xs text-muted-foreground">
                            {number.format(item.scores.average)} pts
                          </span>
                        )}
                    </td>
                  </tr>
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section aria-labelledby="distribution-heading" className="space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <h2 id="distribution-heading" className="text-xl font-semibold">
            Score distribution
          </h2>
          <div className="flex flex-wrap items-center gap-3">
            <Label htmlFor={selectId}>Show scores for</Label>
            <Select
              value={distributionId}
              onValueChange={(value) => {
                if (value !== null) setDistributionId(value);
              }}
            >
              <SelectTrigger id={selectId} className="max-w-full min-w-40">
                <SelectValue>{distributionLabel}</SelectValue>
              </SelectTrigger>
              <SelectContent align="end">
                <SelectItem value="total">Assignment total</SelectItem>
                {questions.map((item) => (
                  <SelectItem
                    key={item.scores.question_id}
                    value={item.scores.question_id}
                  >
                    Question {questionLabel(item.scores.question_id)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        {distribution.question_id !== 'total' && (
          <ScoreSummary scores={distribution} />
        )}
        {distribution.graded_count > 0 ? (
          <>
            <StatisticsChart
              label={`${distributionLabel} score distribution`}
              unit="submissions"
              histogram={distribution.possible_points > 0}
              rows={distribution.buckets.map((bucket) => ({
                label: bucket.label,
                value: bucket.count,
                detail: `${bucket.label}: ${bucket.count} ${bucket.count === 1 ? 'submission' : 'submissions'}`,
              }))}
            />
            <p className="text-sm text-muted-foreground">
              {distribution.graded_count} graded submissions.{' '}
              {distribution.possible_points > 0
                ? 'Each range includes its lower bound. Scores of 100% or more are in the final bar.'
                : 'Scores are shown in points.'}
            </p>
            <details className="text-sm">
              <summary className="w-fit cursor-pointer py-2 underline underline-offset-4">
                View distribution data
              </summary>
              <table className="mt-2 w-full max-w-sm tabular-nums">
                <caption className="sr-only">
                  {distributionLabel} score distribution
                </caption>
                <thead>
                  <tr>
                    <th scope="col" className="py-2 text-start">
                      Score
                    </th>
                    <th scope="col" className="py-2 text-end">
                      Submissions
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {distribution.buckets.map((bucket) => (
                    <tr key={bucket.label} className="border-t">
                      <th scope="row" className="py-2 text-start font-normal">
                        {bucket.label}
                      </th>
                      <td className="py-2 text-end">{bucket.count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          </>
        ) : (
          <p className="text-muted-foreground">
            {distributionId === 'total'
              ? 'Finish grading a submission to see its total in the distribution.'
              : 'Grade this question to see its score distribution.'}
          </p>
        )}
      </section>
    </div>
  );
}

function ScoreSummary({ scores }: { scores: ScoreStatistics }) {
  const metrics = [
    ['Minimum', scores.minimum],
    ['Median', scores.median],
    ['Maximum', scores.maximum],
    ['Mean', scores.average],
    ['Standard deviation', scores.std_dev],
  ] as const;
  return (
    <dl className="grid grid-cols-2 gap-x-6 gap-y-5 border-y py-5 sm:grid-cols-3 xl:grid-cols-5">
      {metrics.map(([label, value]) => (
        <div key={label}>
          <dt className="text-sm text-muted-foreground">{label}</dt>
          <dd className="mt-2 text-2xl font-semibold tracking-tight tabular-nums">
            {value === null ? (
              <span className="text-base font-normal">Not graded</span>
            ) : (
              scoreValue(value, scores.possible_points)
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}
