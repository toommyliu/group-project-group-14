import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ErrorAlert } from '@/components/error-alert';
import { Check, Eraser, PenLine, TriangleAlert } from 'lucide-react';
import {
  answerQuery,
  confirmAiDraft,
  type Answer,
  type Question,
} from '@/api/queries';
import { parseAiDraft } from '@/lib/ai-draft';
import { errorMessage } from '@/lib/error-message';
import {
  rubricEditorQuery,
  rubricEditorChanged,
  type RubricEditorDraft,
} from '@/lib/rubric-editor-draft';
import { useGradeEditor } from '@/hooks/use-grade-editor';
import { useFolderSession } from '@/hooks/use-folder-session';
import { RubricEditSheet } from './rubric-edit-sheet';
import { GradingText } from './grading-text';
import { Button } from './ui/button';
import { Label } from './ui/label';
import { Kbd } from './ui/kbd';
import { Textarea } from './ui/textarea';
import { Input } from './ui/input';
import { GradeRecoveryDialog } from './grade-recovery-dialog';
import { useGradingShortcutHints } from '@/hooks/use-grading-hotkeys';
import { rubricActions } from '@/lib/grading-shortcuts';
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogCancel,
} from './ui/alert-dialog';

export function GradePanel({
  assignmentId,
  question,
  answer,
  navigating,
  onNext,
}: {
  assignmentId: string;
  question: Question;
  answer: Answer;
  navigating: boolean;
  onNext: (() => void) | undefined;
}) {
  const folderSession = useFolderSession();
  const { data: rubricDraft } = useQuery<RubricEditorDraft>(
    rubricEditorQuery(assignmentId, question.id, answer.version ?? null)
  );
  const rubricStatus =
    rubricDraft?.generation.status === 'running'
      ? 'Drafting rubric with AI…'
      : rubricDraft?.generation.status === 'failed'
        ? 'AI rubric draft failed · Open Edit for details'
        : rubricDraft?.aiDraft
          ? 'AI rubric draft ready · Review and save'
          : rubricDraft && rubricEditorChanged(rubricDraft)
            ? 'Rubric changes not saved'
            : null;
  const [editingRubric, setEditingRubric] = useState(false);
  const {
    saved,
    draft,
    recovered,
    recover,
    conflict,
    resolveConflict,
    dismissConflict,
    persist,
    saving,
    feedback,
    scoreInput,
    message,
    folderWarning,
    error,
    dirty,
    validScore,
    score,
    busy,
    change,
    choose,
    clearScore,
    reload,
    requestReload,
    reloadOpen,
    setReloadOpen,
    blocker,
    discardAndLeave,
    retryAndLeave,
  } = useGradeEditor({
    assignmentId,
    question,
    answer,
    navigating,
    onNext,
    paused: editingRubric,
  });
  // An unsaved grade was chosen from the rubric about to change; let it land
  // first so the edit sees it (and can carry it along).
  const rubricLocked = busy || saving || dirty || Boolean(error);
  const { shortcut, shortcutAria } = useGradingShortcutHints();
  const client = useQueryClient();
  const aiDraft = parseAiDraft(saved.analysis);
  const unreviewed = aiDraft !== null && !aiDraft.reviewed;
  const [confirming, setConfirming] = useState(false);
  const [confirmError, setConfirmError] = useState('');
  async function confirmDraft() {
    setConfirming(true);
    setConfirmError('');
    try {
      const result = await confirmAiDraft(assignmentId, saved);
      client.setQueryData(
        answerQuery(assignmentId, answer.submission_id, question.id).queryKey,
        result.answer
      );
    } catch (caught) {
      setConfirmError(errorMessage(caught));
    } finally {
      setConfirming(false);
    }
  }
  return (
    <section
      className="min-w-0"
      aria-labelledby="rubric-heading"
      aria-busy={saving}
    >
      <div className="mb-5 grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1">
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
          <h2 id="rubric-heading" className="font-semibold">
            Rubric
          </h2>
          <Button
            variant="ghost"
            size="sm"
            className="-ms-1.5 self-center text-muted-foreground"
            disabled={rubricLocked}
            title={
              rubricLocked
                ? 'Available once this grade has finished saving'
                : 'Edit this question’s rubric in the answer key'
            }
            onClick={() => setEditingRubric(true)}
          >
            <PenLine aria-hidden="true" />
            Edit
            <span className="sr-only">
              {folderSession ? ' answers and rubric' : ' rubric'}
            </span>
          </Button>
          {rubricStatus && (
            <p role="status" className="text-sm text-muted-foreground">
              {rubricStatus}
            </p>
          )}
          <p
            role="status"
            className="min-w-0 text-sm wrap-anywhere text-muted-foreground"
          >
            {error
              ? 'Not saved'
              : saving
                ? 'Saving…'
                : dirty
                  ? 'Saving…'
                  : unreviewed
                    ? `AI draft${aiDraft.confidence !== null ? ` · ${aiDraft.confidence}% confident` : ''}`
                    : message ||
                      (saved.points_awarded === null ? 'Not graded' : 'Saved')}
          </p>
          {unreviewed && (
            <Button
              size="sm"
              variant="outline"
              className="h-auto min-h-11 max-w-full whitespace-normal focus-visible:ring-2"
              disabled={rubricLocked || confirming}
              onClick={() => void confirmDraft()}
            >
              Confirm AI grade
            </Button>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          <Label htmlFor="grade-score" className="sr-only">
            Score
          </Label>
          <Input
            ref={scoreInput}
            id="grade-score"
            type="number"
            min="0"
            step="any"
            placeholder="—"
            className="field-sizing-content h-11 max-w-28 min-w-16 [appearance:textfield] text-end text-lg font-semibold tabular-nums md:text-lg [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
            value={draft.manual ? draft.score : (score?.toString() ?? '')}
            disabled={busy}
            title={
              shortcut('focusScore')
                ? `Focus score (${shortcut('focusScore')})`
                : undefined
            }
            aria-keyshortcuts={shortcutAria('focusScore')}
            aria-invalid={!validScore}
            aria-describedby={draft.manual ? 'score-hint' : undefined}
            onFocus={(event) => event.currentTarget.select()}
            onChange={(event) =>
              change({
                ...draft,
                labels: [],
                manual: true,
                score: event.target.value,
              })
            }
          />
          <span className="text-sm whitespace-nowrap text-muted-foreground tabular-nums">
            / {question.possible_points}
          </span>
          <Button
            variant="ghost"
            className="size-11"
            aria-label="Clear grade"
            title={
              shortcut('clearGrade')
                ? `Clear grade (${shortcut('clearGrade')})`
                : 'Clear grade'
            }
            disabled={busy || (score === null && !draft.manual)}
            aria-keyshortcuts={shortcutAria('clearGrade')}
            onClick={clearScore}
          >
            <Eraser aria-hidden="true" />
          </Button>
        </div>
        {draft.manual && (
          <p
            id="score-hint"
            className="col-span-2 text-xs text-muted-foreground"
          >
            Custom score. Select a rubric item to score by rubric instead.
          </p>
        )}
        {folderWarning && !error && (
          <p
            role="status"
            className="col-span-2 flex items-start gap-1.5 text-xs text-muted-foreground"
          >
            <TriangleAlert
              className="mt-0.5 size-3.5 shrink-0"
              aria-hidden="true"
            />
            <span className="min-w-0 wrap-anywhere">{folderWarning}</span>
          </p>
        )}
      </div>
      {confirmError && <ErrorAlert className="mb-4" error={confirmError} />}
      {recovered.length > 0 && (
        <div className="mb-4 grid gap-2">
          <p>
            Unsaved work from an earlier visit or another tab. Changing this
            grade discards it.
          </p>
          {recovered.map(({ key, value }, index) => (
            <Button
              key={key}
              variant="outline"
              disabled={busy || saving || dirty}
              onClick={() => recover(value)}
            >
              Recover draft {index + 1}
            </Button>
          ))}
        </div>
      )}
      <fieldset disabled={busy} className="grid min-w-0 gap-2">
        <legend className="sr-only">Rubric choices</legend>
        {question.rubric.map((option, index) => {
          const selected = !draft.manual && draft.labels.includes(option.label);
          return (
            <div
              key={index}
              className={`border has-[>button:focus-visible]:border-ring has-[>button:focus-visible]:ring-1 has-[>button:focus-visible]:ring-ring/50 ${selected ? 'border-foreground bg-muted' : 'border-border'} ${selected && unreviewed ? 'border-dashed' : ''}`}
            >
              <Button
                variant="ghost"
                type="button"
                aria-keyshortcuts={
                  rubricActions[index]
                    ? shortcutAria(rubricActions[index])
                    : undefined
                }
                aria-pressed={selected}
                onClick={() => choose(index)}
                className="block h-auto w-full px-3 py-2.5 text-start whitespace-normal focus-visible:border-transparent focus-visible:ring-0"
              >
                <span className="flex items-start gap-3">
                  <span
                    className="flex min-h-6 min-w-6 shrink-0 items-center justify-center border px-1 text-xs"
                    aria-hidden="true"
                  >
                    {selected ? (
                      <Check size={14} />
                    ) : rubricActions[index] ? (
                      shortcut(rubricActions[index])
                    ) : null}
                  </span>
                  <span className="min-w-0 flex-1 wrap-anywhere">
                    {option.label}
                  </span>
                  <span className="shrink-0 tabular-nums">
                    {option.points_delta > 0 ? '+' : ''}
                    {option.points_delta}
                  </span>
                </span>
              </Button>
              {option.default_comment && (
                <div className="px-3 pb-3 text-xs text-muted-foreground">
                  <GradingText>{option.default_comment}</GradingText>
                </div>
              )}
            </div>
          );
        })}
        {!question.rubric.length && (
          <p className="text-muted-foreground">
            No rubric items. Enter a score below.
          </p>
        )}
      </fieldset>
      {draft.labels.some(
        (label) => !question.rubric.some((option) => option.label === label)
      ) && (
        <p className="mt-3 text-sm" role="alert">
          Some saved rubric items are no longer available. Choose a current item
          before saving.
        </p>
      )}
      <div className="mt-6 grid gap-2">
        <Label
          htmlFor="grading-feedback"
          className="text-base leading-normal font-medium"
        >
          Feedback{' '}
          {shortcut('focusFeedback') && <Kbd>{shortcut('focusFeedback')}</Kbd>}
        </Label>
        <Textarea
          ref={feedback}
          className="field-sizing-fixed resize-y text-base md:text-sm"
          id="grading-feedback"
          aria-keyshortcuts={shortcutAria('focusFeedback')}
          rows={3}
          value={draft.analysis}
          maxLength={50000}
          disabled={busy}
          placeholder="Explain the score or how to improve."
          onChange={(event) =>
            change({ ...draft, analysis: event.target.value })
          }
        />
      </div>
      {error && <ErrorAlert className="mt-2" error={error} />}
      {error && (
        <div className="mt-3 flex gap-2">
          <Button disabled={busy || saving} onClick={() => void persist()}>
            Retry save
          </Button>
          <Button
            variant="outline"
            disabled={busy || saving}
            onClick={requestReload}
          >
            Reload saved answer
          </Button>
        </div>
      )}
      <AlertDialog
        open={conflict !== null}
        onOpenChange={(open) => {
          if (!open && !saving) dismissConflict();
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>This answer changed</AlertDialogTitle>
            <AlertDialogDescription>
              Compare the saved answer with your changes before choosing which
              to keep.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="grid gap-4 text-sm">
            <div>
              <p className="font-medium">
                Saved answer: {conflict?.points_awarded ?? 'Ungraded'}
              </p>
              <p>{conflict?.rubric_labels.join(', ')}</p>
              <p className="max-h-40 overflow-auto whitespace-pre-wrap">
                {conflict?.analysis}
              </p>
            </div>
            <div>
              <p className="font-medium">Your changes: {score ?? 'Ungraded'}</p>
              <p>{draft.labels.join(', ')}</p>
              <p className="max-h-40 overflow-auto whitespace-pre-wrap">
                {draft.analysis}
              </p>
            </div>
          </div>
          <AlertDialogFooter className="flex-wrap">
            <AlertDialogCancel disabled={saving}>
              Keep editing
            </AlertDialogCancel>
            <Button
              variant="outline"
              disabled={saving}
              onClick={() => void resolveConflict(false)}
            >
              Use saved answer
            </Button>
            <Button
              disabled={saving}
              onClick={() => void resolveConflict(true)}
            >
              Save my changes
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <RubricEditSheet
        assignmentId={assignmentId}
        target={
          editingRubric
            ? { questionId: question.id, version: answer.version }
            : null
        }
        onClose={() => setEditingRubric(false)}
      />
      <GradeRecoveryDialog
        mode={
          reloadOpen ? 'reload' : blocker.status === 'blocked' ? 'leave' : null
        }
        busy={busy || saving}
        onCancel={() => {
          setReloadOpen(false);
          blocker.reset?.();
        }}
        onDiscard={reloadOpen ? () => void reload() : discardAndLeave}
        onRetry={() => void retryAndLeave()}
      />
    </section>
  );
}
