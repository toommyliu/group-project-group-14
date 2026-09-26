import { Fragment, useId, useRef, useState } from 'react';
import { Link } from '@tanstack/react-router';
import { ChartColumn } from 'lucide-react';
import type {
  AssignmentVersionStatistics,
  ScoreStatistics,
} from '@/api/queries';
import { StatisticsChart } from './statistics-chart';
import { Button } from './ui/button';
import { Label } from './ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './ui/select';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';

const number = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });
const percent = new Intl.NumberFormat(undefined, {
  style: 'percent',
  maximumFractionDigits: 0,
});
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
  const distributionHeading = useRef<HTMLHeadingElement>(null);
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
        <GradingProgress report={report} />
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
          reference={
            scores.average_percent === null
              ? undefined
              : {
                  value: scores.average_percent,
                  label: 'Assignment mean',
                  detail: `Assignment mean: ${number.format(scores.average_percent)}%`,
                }
          }
          rows={questions.map(({ scores: item }) => ({
            label: questionLabel(item.question_id),
            value: item.average_percent,
            detail: `Question ${questionLabel(item.question_id)}: ${scoreValue(item.average, item.possible_points)} mean, ${item.graded_count} of ${item.total_submissions} graded`,
          }))}
        />
        <div className="relative overflow-x-auto">
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
                <th scope="col" className="px-3 py-3 text-end font-medium">
                  Mean
                </th>
                <th scope="col" className="px-3 py-3 text-end font-medium">
                  Full credit
                </th>
                <th scope="col" className="px-3 py-3 text-end font-medium">
                  Zero points
                </th>
                <th scope="col" className="py-3 ps-3">
                  <span className="sr-only">Distribution</span>
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
                          colSpan={7}
                          scope="row"
                          className="pt-5 pb-2 text-start font-medium [overflow-wrap:anywhere]"
                        >
                          {questionLabel(item.parent_id)}. {item.parent_prompt}
                        </th>
                      </tr>
                    )}
                  <tr className="border-b">
                    <th
                      scope="row"
                      className={`max-w-md min-w-64 py-4 pe-4 text-start font-normal [overflow-wrap:anywhere] ${item.parent_id ? 'ps-4' : ''}`}
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
                    <td className="px-3 py-4 text-end whitespace-nowrap tabular-nums">
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
                    <td className="px-3 py-4 text-end whitespace-nowrap tabular-nums">
                      <ShareCell
                        count={item.scores.full_credit_count}
                        total={item.scores.graded_count}
                        applies={item.scores.possible_points > 0}
                      />
                    </td>
                    <td className="px-3 py-4 text-end whitespace-nowrap tabular-nums">
                      <ShareCell
                        count={item.scores.zero_score_count}
                        total={item.scores.graded_count}
                      />
                    </td>
                    <td className="py-4 ps-3 text-end">
                      <Tooltip>
                        <TooltipTrigger
                          render={
                            <Button
                              variant="ghost"
                              size="icon"
                              className="text-muted-foreground"
                              aria-label={`Show score distribution for question ${questionLabel(item.scores.question_id)}`}
                              onClick={() => {
                                setDistributionId(item.scores.question_id);
                                const heading = distributionHeading.current;
                                heading?.scrollIntoView({
                                  block: 'start',
                                  behavior: 'instant',
                                });
                                heading?.focus({ preventScroll: true });
                              }}
                            />
                          }
                        >
                          <ChartColumn aria-hidden="true" />
                        </TooltipTrigger>
                        <TooltipContent>Show distribution</TooltipContent>
                      </Tooltip>
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
          <h2
            id="distribution-heading"
            ref={distributionHeading}
            tabIndex={-1}
            className="scroll-mt-6 text-xl font-semibold outline-none"
          >
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
                ? 'Each range includes its lower bound.'
                : 'Scores are shown in points.'}
            </p>
            {/* The chart is hover-only, so screen readers get the data here. */}
            <div className="sr-only">
              <table>
                <caption>{distributionLabel} score distribution</caption>
                <thead>
                  <tr>
                    <th scope="col">Score</th>
                    <th scope="col">Submissions</th>
                    <th scope="col">Share</th>
                  </tr>
                </thead>
                <tbody>
                  {distribution.buckets.map((bucket) => (
                    <tr key={bucket.label}>
                      <th scope="row">{bucket.label}</th>
                      <td>{bucket.count}</td>
                      <td>
                        {percent.format(
                          bucket.count / distribution.graded_count
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
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

function GradingProgress({ report }: { report: AssignmentVersionStatistics }) {
  const { scores, partial_count, ungraded_count } = report;
  const total = scores.total_submissions;
  const segments = [
    {
      label: `${scores.graded_count} of ${total} submissions fully graded`,
      count: scores.graded_count,
      className: 'bg-chart-1',
    },
    {
      label: `${partial_count} partially graded`,
      count: partial_count,
      className: 'bg-chart-1/45',
    },
    {
      label: `${ungraded_count} ungraded`,
      count: ungraded_count,
      className: 'bg-chart-1/15',
    },
  ];
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2 tabular-nums">
        <p className="font-medium">
          {number.format(scores.possible_points)} points possible
        </p>
        {total > 0 &&
          segments.map(
            (segment, index) =>
              (index === 0 || segment.count > 0) && (
                <p
                  key={segment.className}
                  className="flex items-center gap-2 text-muted-foreground"
                >
                  <span
                    className={`size-2.5 shrink-0 rounded-full ${segment.className}`}
                    aria-hidden="true"
                  />
                  {segment.label}
                </p>
              )
          )}
      </div>
      <div
        className={`flex h-2 overflow-hidden rounded-full ${total === 0 ? 'bg-chart-1/10 text-chart-1/35' : 'bg-chart-1/15'}`}
        style={
          total === 0
            ? {
                backgroundImage:
                  'repeating-linear-gradient(135deg, currentColor 0 2px, transparent 2px 8px)',
              }
            : undefined
        }
        aria-hidden="true"
      >
        {total > 0 &&
          segments
            .slice(0, 2)
            .map((segment) => (
              <div
                key={segment.className}
                className={segment.className}
                style={{ width: `${(segment.count / total) * 100}%` }}
              />
            ))}
      </div>
    </div>
  );
}

function ShareCell({
  count,
  total,
  applies = true,
}: {
  count: number;
  total: number;
  applies?: boolean;
}) {
  if (!applies || total === 0)
    return <span className="text-muted-foreground">—</span>;
  return (
    <>
      {percent.format(count / total)}
      <span className="block text-xs text-muted-foreground">
        {count} of {total}
      </span>
    </>
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
  const graded = scores.graded_count;
  const details = [
    scores.q1 !== null &&
      scores.q3 !== null && [
        'Middle 50%',
        `${scoreValue(scores.q1, scores.possible_points)} – ${scoreValue(scores.q3, scores.possible_points)}`,
      ],
    graded > 0 &&
      scores.possible_points > 0 && [
        'Full credit',
        `${scores.full_credit_count} (${percent.format(scores.full_credit_count / graded)})`,
      ],
    graded > 0 && [
      'Zero points',
      `${scores.zero_score_count} (${percent.format(scores.zero_score_count / graded)})`,
    ],
  ].filter((detail) => detail !== false);
  return (
    <div className="space-y-4 border-y py-5">
      <dl className="flex flex-wrap gap-x-6 gap-y-5">
        {metrics.map(([label, value]) => (
          <div
            key={label}
            className="max-w-full min-w-[min(100%,8rem)] flex-1 basis-auto"
          >
            <dt className="text-sm text-muted-foreground">{label}</dt>
            <dd className="mt-2 text-2xl font-semibold tracking-tight [overflow-wrap:anywhere] tabular-nums">
              {value === null ? (
                <span className="text-base font-normal">Not graded</span>
              ) : (
                scoreValue(value, scores.possible_points)
              )}
            </dd>
          </div>
        ))}
      </dl>
      {details.length > 0 && (
        <dl className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
          {details.map(([label, value]) => (
            <div
              key={label}
              className="flex max-w-full min-w-0 flex-wrap gap-x-2"
            >
              <dt className="text-muted-foreground">{label}</dt>
              <dd className="min-w-0 [overflow-wrap:anywhere] tabular-nums">
                {value}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}
