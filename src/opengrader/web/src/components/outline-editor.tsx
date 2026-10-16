import { useEffect, useRef, useState, type ReactNode, type Ref } from 'react';
import { useBlocker } from '@tanstack/react-router';
import { useQueryClient } from '@tanstack/react-query';
import {
  ArrowDownUp,
  CircleAlert,
  Crosshair,
  Download,
  EllipsisVertical,
  FileUp,
  IdCard,
  LoaderCircle,
  Lock,
  Plus,
  SquareUser,
  WandSparkles,
} from 'lucide-react';
import { cn } from 'cn';
import {
  generateTemplate,
  overlayUrl,
  replaceTemplate,
  retryRecut,
  saveOutline,
  templateUrl,
  type AssignmentOutline,
} from '@/api/outline';
import { errorMessage } from '@/lib/error-message';
import { useMediaQuery } from '@/hooks/use-media-query';
import {
  NAME,
  STUDENT_ID,
  addPart,
  addQuestion,
  boxChanges,
  draftFromOutline,
  findRow,
  moveQuestion,
  movePart,
  outlineFromDraft,
  ownerBoxes,
  questionPoints,
  removeRow,
  rows,
  sameOutline,
  setOwnerBoxes,
  sortByPosition,
  totalPoints,
  updateRow,
  type Box,
  type BoxChanges,
  type Draft,
  type Owner,
  type Row,
} from '@/lib/outline-draft';
import { ErrorAlert } from './error-alert';
import { Alert, AlertDescription, AlertTitle } from './ui/alert';
import { FileUpload } from './file-upload';
import { RecutConfirm, RecutProgress } from './outline-recut';
import {
  OutlineTemplate,
  type BoxSelection,
  type Interaction,
} from './outline-template';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Slider } from './ui/slider';
import { Switch } from './ui/switch';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLinkItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from './ui/dropdown-menu';
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from './ui/resizable';

const readOnlyField =
  'border-transparent bg-transparent shadow-none focus-visible:ring-0 dark:bg-transparent';

const NEW_QUESTION = 'new-question';

type Editing = 'all' | 'boxes' | 'none';

const identityDefaults: Record<string, Box> = {
  [NAME]: { page: 1, x: 0.1, y: 0.04, width: 0.5, height: 0.05 },
  [STUDENT_ID]: { page: 1, x: 0.1, y: 0.1, width: 0.5, height: 0.05 },
};

export function OutlineEditor({
  assignmentId,
  outline,
  settled,
  paused,
  onDirtyChange,
}: {
  assignmentId: string;
  outline: AssignmentOutline;
  settled: boolean;
  paused: boolean;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const client = useQueryClient();
  const [saved, setSaved] = useState(outline);
  const [baseline, setBaseline] = useState(() =>
    draftFromOutline(outline.outline)
  );
  const [draft, setDraft] = useState(baseline);
  const [selected, setSelected] = useState<BoxSelection | null>(null);
  const [activeRow, setActiveRow] = useState<string | null>(null);
  const [armed, setArmed] = useState<Owner | null>(null);
  const [focus, setFocus] = useState<{
    owner: Owner;
    nonce: number;
    index?: number;
  } | null>(null);
  const [busy, setBusy] = useState<'' | 'saving' | 'template' | 'retrying'>('');
  const [confirming, setConfirming] = useState<{
    open: boolean;
    changes: BoxChanges;
    identityBoxes: boolean;
    error: string;
  } | null>(null);
  const [error, setError] = useState('');
  const [retried, setRetried] = useState(false);
  const [notice, setNotice] = useState('');
  const fileInput = useRef<HTMLInputElement>(null);
  const focusCount = useRef(0);
  const newQuestion = useRef<HTMLButtonElement>(null);
  const [titleRequest, setTitleRequest] = useState<{
    key: string;
    nonce: number;
  } | null>(null);
  const handledTitle = useRef(0);
  const identityButtons = useRef(new Map<string, HTMLButtonElement>());
  const titleInputs = useRef(new Map<string, HTMLInputElement>());
  const writing =
    outline.template?.sha256 === saved.template?.sha256
      ? (outline.writing ?? null)
      : null;
  const written = writing?.submissions ?? 0;
  const [overlay, setOverlay] = useState({
    shown: false,
    opacity: 80,
    failed: '',
    attempt: 0,
  });
  const wide = useMediaQuery('(min-width: 65rem)');

  const lock = outline.lock;
  const mode: Editing =
    lock === null ? 'all' : lock.scope === 'questions' ? 'boxes' : 'none';
  const working = !!busy || paused;
  const baselineHasQuestions = baseline.questions.length > 0;
  const dirty = !sameOutline(draft, baseline);

  const changed = settled && !working && outline.revision !== saved.revision;
  if (changed && !dirty) accept(outline);

  useEffect(() => {
    if (!titleRequest || titleRequest.nonce === handledTitle.current) return;
    handledTitle.current = titleRequest.nonce;
    const { key } = titleRequest;
    (key === NEW_QUESTION
      ? newQuestion.current
      : (titleInputs.current.get(key) ?? identityButtons.current.get(key))
    )?.focus();
  }, [titleRequest]);
  useEffect(() => onDirtyChange(dirty), [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);
  const allRows = rows(draft);
  const missing = allRows.filter((row) => row.leaf && !row.leaf.boxes.length);

  useBlocker({
    shouldBlockFn: () =>
      dirty && !window.confirm('Discard unsaved outline changes?'),
    enableBeforeUnload: () => dirty,
  });

  function accept(next: AssignmentOutline, message = '') {
    const fresh = draftFromOutline(next.outline);
    setSaved(next);
    setBaseline(fresh);
    setDraft(fresh);
    setSelected(null);
    setArmed(null);
    setFocus(null);
    setNotice(message);
  }

  async function save() {
    setBusy('saving');
    setError('');
    setRetried(false);
    try {
      accept(
        await saveOutline(assignmentId, saved, outlineFromDraft(draft)),
        'Outline saved.'
      );
      void client.invalidateQueries({ queryKey: ['assignment-summaries'] });
      await client.invalidateQueries({
        queryKey: ['assignments', assignmentId],
      });
      return '';
    } catch (cause) {
      return errorMessage(cause, 'Could not save the outline. Try again.');
    } finally {
      setBusy('');
    }
  }

  function requestSave() {
    if (mode === 'boxes' && outline.submissions)
      setConfirming({
        open: true,
        changes: boxChanges(baseline, draft),
        identityBoxes: draft.name !== null || draft.studentId !== null,
        error: '',
      });
    else void save().then(setError);
  }

  async function confirmRecut() {
    if (!confirming) return;
    const failure = await save();
    setConfirming({ ...confirming, open: failure !== '', error: failure });
  }

  async function retry() {
    setBusy('retrying');
    setError('');
    try {
      await retryRecut(assignmentId, saved.version);
      await client.invalidateQueries({
        queryKey: ['assignments', assignmentId],
      });
      setRetried(true);
    } catch (cause) {
      setError(errorMessage(cause, 'Could not try again. Reload the page.'));
    } finally {
      setBusy('');
    }
  }

  async function template(
    action: () => Promise<AssignmentOutline>,
    done: string,
    confirmation = dirty ? 'Discard unsaved outline changes?' : ''
  ) {
    if (confirmation && !window.confirm(confirmation)) return;
    setBusy('template');
    setError('');
    try {
      accept(await action(), done);
      await client.invalidateQueries({
        queryKey: ['assignments', assignmentId],
      });
    } catch (cause) {
      setError(
        errorMessage(cause, 'Could not update the template. Try again.')
      );
    } finally {
      setBusy('');
    }
  }

  function upload(files: File[]) {
    const file = files[0];
    if (!file) return;
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      setError('Choose a PDF file.');
      return;
    }
    void template(
      () => replaceTemplate(assignmentId, saved.version, file),
      `Template replaced with ${file.name}.`
    );
  }

  function edit(next: Draft) {
    setDraft(next);
    setNotice('');
  }

  function activate(key: string | null, scroll = true) {
    setActiveRow(key);
    if (key && scroll) setFocus({ owner: key, nonce: ++focusCount.current });
  }

  function drawTarget() {
    const row = findRow(draft, activeRow);
    return armed ?? (row?.leaf && !row.leaf.boxes.length ? row.key : null);
  }

  const interaction: Interaction =
    mode === 'none' || working
      ? 'view'
      : mode === 'all' || drawTarget()
        ? 'draw'
        : 'adjust';

  function draw(box: Box) {
    const target = drawTarget();
    if (target) {
      const boxes = ownerBoxes(draft, target);
      edit(setOwnerBoxes(draft, target, [...boxes, box]));
      setSelected({ owner: target, index: boxes.length });
      setActiveRow(target);
      setArmed(null);
      return;
    }
    const [next, key] = addQuestion(draft, [box]);
    edit(next);
    setSelected({ owner: key, index: 0 });
    setActiveRow(key);
    focusTitle(key);
  }

  function focusTitle(key: string) {
    setTitleRequest({ key, nonce: ++focusCount.current });
  }

  function identity(owner: typeof NAME | typeof STUDENT_ID) {
    if (!ownerBoxes(draft, owner).length)
      edit(setOwnerBoxes(draft, owner, [identityDefaults[owner]]));
    setSelected({ owner, index: 0 });
    setActiveRow(owner);
    setFocus({ owner, nonce: ++focusCount.current, index: 0 });
  }

  function place(owner: Owner) {
    const boxes = ownerBoxes(draft, owner);
    const last =
      boxes.at(-1) ?? allRows.flatMap((row) => row.leaf?.boxes ?? []).at(-1);
    const height = Math.min(last?.height ?? 0.12, 0.15);
    const box = last
      ? {
          page: last.page,
          x: last.x,
          width: last.width,
          height,
          y: Math.min(last.y + last.height + 0.01, 1 - height),
        }
      : { page: 1, x: 0.1, y: 0.2, width: 0.8, height };
    edit(setOwnerBoxes(draft, owner, [...boxes, box]));
    setSelected({ owner, index: boxes.length });
    setActiveRow(owner);
    setArmed(null);
    setFocus({ owner, nonce: ++focusCount.current, index: boxes.length });
  }

  function select(selection: BoxSelection | null) {
    setSelected(selection);
    if (selection) setActiveRow(selection.owner);
  }

  if (!saved.template)
    return (
      <div className="grid max-w-2xl gap-4">
        {lock && <p className="text-muted-foreground">{lock.reason}</p>}
        <FileUpload
          title="Upload the assignment PDF"
          description="Use the blank assignment students write on. Next you draw a box around each question on it."
          buttonLabel={busy ? 'Uploading…' : 'Select PDF'}
          inputLabel="Upload template PDF"
          accept=".pdf,application/pdf"
          disabled={mode !== 'all' || working}
          error={error}
          onFiles={upload}
        />
        {baselineHasQuestions && (
          <div className="grid gap-2 border p-4">
            <h2 className="text-sm font-medium">Or use the generated PDF</h2>
            <p className="text-sm text-muted-foreground">
              Print the PDF Opengrader makes from the answer key. Every
              question's box is drawn for you.
            </p>
            <Button
              type="button"
              variant="outline"
              className="justify-self-start"
              disabled={mode !== 'all' || working}
              onClick={() =>
                void template(
                  () => generateTemplate(assignmentId, saved.version),
                  'Generated the template and drew every box.'
                )
              }
            >
              <WandSparkles aria-hidden="true" />
              Use generated PDF
            </Button>
          </div>
        )}
      </div>
    );

  const templateMenu = (
    <DropdownMenu>
      <DropdownMenuTrigger
        disabled={working}
        render={
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="-me-2 size-11 shrink-0"
            aria-label="Template options"
            title="Template options"
          />
        }
      >
        <EllipsisVertical aria-hidden="true" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-60">
        <DropdownMenuLinkItem
          href={templateUrl(assignmentId, saved)}
          target="_blank"
          rel="noreferrer"
        >
          <Download aria-hidden="true" />
          Open {saved.template.filename}
        </DropdownMenuLinkItem>
        {mode === 'all' && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => fileInput.current?.click()}>
              <FileUp aria-hidden="true" />
              Replace PDF…
            </DropdownMenuItem>
            {baselineHasQuestions && (
              <DropdownMenuItem
                onClick={() =>
                  void template(
                    () => generateTemplate(assignmentId, saved.version),
                    'Generated the template and drew every box.',
                    'Replace the template and every box with the PDF generated from the answer key?'
                  )
                }
              >
                <WandSparkles aria-hidden="true" />
                Use generated PDF
              </DropdownMenuItem>
            )}
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 pb-4">
      <input
        ref={fileInput}
        type="file"
        accept=".pdf,application/pdf"
        className="hidden"
        aria-label="Replace template PDF"
        onChange={(event) => {
          upload(Array.from(event.target.files ?? []));
          event.target.value = '';
        }}
      />
      {error && <ErrorAlert error={error} />}
      {outline.recut && (
        <RecutProgress
          recut={outline.recut}
          retrying={busy === 'retrying'}
          retried={retried}
          onRetry={() => void retry()}
        />
      )}
      {lock?.scope === 'outline' && dirty && (
        <ErrorAlert
          title="Your changes can't be saved right now"
          error={lock.reason}
        />
      )}
      {changed && dirty && mode !== 'none' && (
        <Alert>
          <CircleAlert aria-hidden="true" />
          <AlertTitle>This outline changed since you opened it</AlertTitle>
          <AlertDescription className="flex flex-wrap items-center gap-x-3 gap-y-1">
            Your changes can't be saved over the newer outline.
            <Button
              type="button"
              variant="link"
              className="h-auto p-0"
              onClick={() => {
                if (
                  window.confirm(
                    'Discard your changes and load the latest outline?'
                  )
                )
                  accept(outline);
              }}
            >
              Load the latest outline
            </Button>
          </AlertDescription>
        </Alert>
      )}
      <ResizablePanelGroup
        orientation={wide ? 'horizontal' : 'vertical'}
        className="min-h-0 flex-1 border"
      >
        <ResizablePanel
          id="template"
          minSize="35%"
          className="flex min-w-0 flex-col"
        >
          <PaneHeader
            title="Template"
            detail={`${saved.template.filename} · ${saved.template.page_count} ${saved.template.page_count === 1 ? 'page' : 'pages'}`}
          >
            {written > 0 && (
              <div className="flex shrink-0 items-center gap-2">
                <Switch
                  id="writing-overlay"
                  checked={overlay.shown}
                  onCheckedChange={(shown) =>
                    setOverlay((current) => ({ ...current, shown }))
                  }
                />
                <Label htmlFor="writing-overlay">Student writing</Label>
              </div>
            )}
            {templateMenu}
          </PaneHeader>
          {written > 0 && overlay.shown && (
            <div className="flex h-10 shrink-0 items-center gap-3 border-b bg-muted/40 px-4 text-xs text-muted-foreground">
              Opacity
              <div className="w-28">
                <Slider
                  min={10}
                  max={100}
                  step={5}
                  value={[overlay.opacity]}
                  getAriaLabel={() => 'Overlay opacity'}
                  onValueChange={(value) =>
                    setOverlay((current) => ({
                      ...current,
                      opacity: Array.isArray(value) ? value[0] : value,
                    }))
                  }
                />
              </div>
              {writing && overlay.failed === writing.revision ? (
                <p
                  role="alert"
                  className="ms-auto flex items-center gap-2 text-destructive"
                >
                  Could not load where students wrote.
                  <Button
                    type="button"
                    variant="link"
                    size="sm"
                    className="h-auto p-0 text-xs"
                    onClick={() =>
                      setOverlay((current) => ({
                        ...current,
                        failed: '',
                        attempt: current.attempt + 1,
                      }))
                    }
                  >
                    Try again
                  </Button>
                </p>
              ) : (
                <p className="ms-auto flex items-center gap-2">
                  {written === 1 ? (
                    'Orange shows where the student wrote.'
                  ) : (
                    <>
                      1 student
                      <span
                        aria-hidden="true"
                        className="h-2 w-16 bg-linear-to-r from-amber-500 via-red-600 to-red-900"
                      />
                      All {written}
                    </>
                  )}
                </p>
              )}
            </div>
          )}
          <div className="min-h-0 flex-1">
            <OutlineTemplate
              key={templateUrl(assignmentId, saved)}
              url={templateUrl(assignmentId, saved)}
              draft={draft}
              selected={selected}
              activeRow={activeRow}
              interaction={interaction}
              focus={focus}
              onSelect={select}
              onDraw={draw}
              onChange={(target, box) => {
                const boxes = [...ownerBoxes(draft, target.owner)];
                boxes[target.index] = box;
                edit(setOwnerBoxes(draft, target.owner, boxes));
              }}
              onRemove={(target) => {
                const boxes = ownerBoxes(draft, target.owner).filter(
                  (_, index) => index !== target.index
                );
                edit(setOwnerBoxes(draft, target.owner, boxes));
                if (boxes.length) {
                  const index = Math.min(target.index, boxes.length - 1);
                  setSelected({ owner: target.owner, index });
                  setFocus({
                    owner: target.owner,
                    nonce: ++focusCount.current,
                    index,
                  });
                } else {
                  setSelected(null);
                  focusTitle(target.owner);
                }
              }}
              onLeave={(owner) => {
                setSelected(null);
                focusTitle(owner);
              }}
              overlay={
                writing && overlay.shown
                  ? {
                      url: (page) =>
                        overlayUrl(
                          assignmentId,
                          saved.version,
                          writing.revision,
                          page
                        ),
                      opacity: overlay.opacity / 100,
                      attempt: overlay.attempt,
                      onError: () =>
                        setOverlay((current) => ({
                          ...current,
                          failed: writing.revision,
                        })),
                    }
                  : null
              }
            />
          </div>
        </ResizablePanel>
        <ResizableHandle />
        <ResizablePanel
          id="outline"
          defaultSize={wide ? '38%' : '45%'}
          minSize={wide ? '24rem' : '30%'}
          className="flex min-w-0 flex-col"
        >
          <PaneHeader
            title="Questions"
            detail={`${allRows.filter((row) => row.leaf).length} graded · ${totalPoints(draft)} points`}
          >
            {mode !== 'all' && (
              <span className="flex shrink-0 items-center gap-1.5 text-xs text-muted-foreground">
                <Lock aria-hidden="true" className="size-3.5" />
                {mode === 'boxes' ? 'Questions locked' : 'Read only'}
              </span>
            )}
          </PaneHeader>
          <div className="custom-scrollbar min-h-0 flex-1 overflow-y-auto px-4 py-3">
            {mode !== 'none' && (
              <p className="mb-3 text-xs text-muted-foreground">
                {mode === 'all'
                  ? 'Drag on the template to add a question. To draw the box for an existing question, select it here first, then drag.'
                  : 'Drag a box or its corners to change what graders see. To redraw a box, delete it, select its question here, then drag on the template.'}
              </p>
            )}
            <div className="mb-3 flex flex-wrap gap-2">
              <IdentityButton
                ref={(element) => {
                  if (element) identityButtons.current.set(NAME, element);
                }}
                label="Name box"
                icon={<SquareUser aria-hidden="true" />}
                placed={!!draft.name}
                active={activeRow === NAME}
                disabled={(mode === 'none' && !draft.name) || working}
                onClick={() => identity(NAME)}
              />
              <IdentityButton
                ref={(element) => {
                  if (element) identityButtons.current.set(STUDENT_ID, element);
                }}
                label="ID box"
                icon={<IdCard aria-hidden="true" />}
                placed={!!draft.studentId}
                active={activeRow === STUDENT_ID}
                disabled={(mode === 'none' && !draft.studentId) || working}
                onClick={() => identity(STUDENT_ID)}
              />
            </div>
            {allRows.length === 0 ? (
              <p className="border border-dashed px-4 py-8 text-center text-muted-foreground">
                No questions yet. Drag a box around the first question on the
                template.
              </p>
            ) : (
              <ol className="grid gap-1" aria-label="Questions">
                {allRows.map((row) => (
                  <OutlineRow
                    key={row.key}
                    row={row}
                    active={activeRow === row.key}
                    armed={armed === row.key}
                    editing={working ? 'none' : mode}
                    inputRef={(element) => {
                      if (element) titleInputs.current.set(row.key, element);
                      else titleInputs.current.delete(row.key);
                    }}
                    onActivate={() => activate(row.key)}
                    onChange={(change) =>
                      edit(updateRow(draft, row.key, change))
                    }
                    onArm={() => {
                      setArmed(row.key);
                      setActiveRow(row.key);
                    }}
                    onPlace={() => place(row.key)}
                    onAddPart={() => {
                      const [next, key] = addPart(draft, row.question.key);
                      edit(next);
                      setActiveRow(key);
                      focusTitle(key);
                    }}
                    onMove={(offset) =>
                      edit(
                        row.key === row.question.key
                          ? moveQuestion(draft, row.key, offset)
                          : movePart(draft, row.key, offset)
                      )
                    }
                    onRemove={() => {
                      const next = removeRow(draft, row.key);
                      const kept = new Set(rows(next).map((item) => item.key));
                      const index = allRows.indexOf(row);
                      const successor =
                        allRows
                          .slice(index + 1)
                          .find((item) => kept.has(item.key)) ??
                        allRows
                          .slice(0, index)
                          .findLast((item) => kept.has(item.key));
                      edit(next);
                      setSelected(null);
                      if (activeRow === row.key) setActiveRow(null);
                      focusTitle(successor?.key ?? NEW_QUESTION);
                    }}
                  />
                ))}
              </ol>
            )}
            {mode === 'all' && (
              <div className="mt-3 flex flex-wrap gap-2">
                <Button
                  ref={newQuestion}
                  type="button"
                  variant="outline"
                  disabled={working}
                  onClick={() => {
                    const [next, key] = addQuestion(draft);
                    edit(next);
                    setActiveRow(key);
                    focusTitle(key);
                  }}
                >
                  <Plus aria-hidden="true" />
                  New question
                </Button>
                {draft.questions.length > 1 && (
                  <Button
                    type="button"
                    variant="ghost"
                    disabled={working}
                    onClick={() => edit(sortByPosition(draft))}
                  >
                    <ArrowDownUp aria-hidden="true" />
                    Number by position
                  </Button>
                )}
              </div>
            )}
          </div>
          {mode !== 'none' && (
            <div className="flex flex-wrap items-center gap-2 border-t px-4 py-3">
              <p
                role="status"
                className="min-w-0 flex-1 text-xs text-muted-foreground"
              >
                {busy === 'saving'
                  ? 'Saving outline…'
                  : busy === 'template'
                    ? 'Updating template…'
                    : dirty
                      ? missing.length
                        ? `Unsaved changes. ${missing.length} ${missing.length === 1 ? 'question has' : 'questions have'} no box yet.`
                        : 'Unsaved changes.'
                      : notice ||
                        (missing.length
                          ? `${missing.length} ${missing.length === 1 ? 'question has' : 'questions have'} no box, so graders will have nothing to see for ${missing.length === 1 ? 'it' : 'them'}.`
                          : 'All changes saved.')}
              </p>
              <Button
                type="button"
                variant="ghost"
                disabled={!dirty || working}
                onClick={() => {
                  setDraft(baseline);
                  setSelected(null);
                  setArmed(null);
                }}
              >
                Discard
              </Button>
              <Button
                type="button"
                disabled={!dirty || working}
                onClick={requestSave}
              >
                {busy === 'saving' && (
                  <LoaderCircle
                    className="motion-safe:animate-spin"
                    aria-hidden="true"
                  />
                )}
                Save outline
              </Button>
            </div>
          )}
        </ResizablePanel>
      </ResizablePanelGroup>
      {outline.submissions && confirming && (
        <RecutConfirm
          open={confirming.open}
          submissions={outline.submissions}
          changes={confirming.changes}
          identityBoxes={confirming.identityBoxes}
          busy={busy === 'saving'}
          error={confirming.error}
          onOpenChange={(open) => setConfirming({ ...confirming, open })}
          onConfirm={() => void confirmRecut()}
        />
      )}
    </div>
  );
}

function PaneHeader({
  title,
  detail,
  children,
}: {
  title: string;
  detail: string;
  children?: ReactNode;
}) {
  return (
    <div className="flex h-14 shrink-0 items-center gap-3 border-b px-4">
      <div className="flex min-w-0 flex-1 items-baseline gap-2">
        <h2 className="shrink-0 font-medium">{title}</h2>
        <span
          className="truncate text-xs text-muted-foreground tabular-nums"
          title={detail}
        >
          {detail}
        </span>
      </div>
      {children}
    </div>
  );
}

function IdentityButton({
  ref,
  label,
  icon,
  placed,
  active,
  disabled,
  onClick,
}: {
  ref: Ref<HTMLButtonElement>;
  label: string;
  icon: ReactNode;
  placed: boolean;
  active: boolean;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <Button
      ref={ref}
      type="button"
      variant="outline"
      size="lg"
      aria-pressed={active}
      disabled={disabled}
      className={cn(active && 'border-amber-600')}
      onClick={onClick}
    >
      {icon}
      {placed ? label : `Add ${label.toLowerCase()}`}
    </Button>
  );
}

function OutlineRow({
  row,
  active,
  armed,
  editing,
  inputRef,
  onActivate,
  onChange,
  onArm,
  onPlace,
  onAddPart,
  onMove,
  onRemove,
}: {
  row: Row;
  active: boolean;
  armed: boolean;
  editing: Editing;
  inputRef: (element: HTMLInputElement | null) => void;
  onActivate: () => void;
  onChange: (change: { title?: string; points?: number }) => void;
  onArm: () => void;
  onPlace: () => void;
  onAddPart: () => void;
  onMove: (offset: number) => void;
  onRemove: () => void;
}) {
  const part = row.question.key !== row.key;
  const parent = !row.leaf;
  const item = row.leaf ?? row.question;
  const [points, setPoints] = useState<string | null>(null);
  const [handingOff, setHandingOff] = useState(false);
  const readOnly = editing !== 'all';
  return (
    <li
      className={cn(
        'grid grid-cols-[2.5rem_minmax(0,1fr)_4.5rem_2rem] items-center gap-2 border border-transparent px-1 py-1',
        part && 'ps-6',
        active && 'border-sky-700/40 bg-sky-500/10',
        armed && 'border-dashed border-sky-700'
      )}
      onFocusCapture={onActivate}
      onPointerDown={onActivate}
    >
      <span className="text-sm font-medium tabular-nums">{row.label}</span>
      <div className="min-w-0">
        <Input
          ref={inputRef}
          value={item.title}
          readOnly={readOnly}
          placeholder={part ? 'Untitled part' : 'Untitled question'}
          aria-label={`Question ${row.label} title`}
          className={cn('h-9 text-sm md:text-sm', readOnly && readOnlyField)}
          onChange={(event) => onChange({ title: event.target.value })}
        />
        {row.leaf && !row.leaf.boxes.length && (
          <p className="mt-1 flex items-center gap-1 text-xs text-amber-700 dark:text-amber-500">
            <CircleAlert className="size-3.5" aria-hidden="true" />
            {armed ? 'Drag on the template to place it.' : 'No box yet'}
          </p>
        )}
        {armed && row.leaf && row.leaf.boxes.length > 0 && (
          <p className="mt-1 text-xs text-sky-800 dark:text-sky-400">
            Drag on the template to add another box.
          </p>
        )}
      </div>
      {parent ? (
        <span
          className="pe-2 text-end text-sm text-muted-foreground tabular-nums"
          aria-label={`Question ${row.label} points`}
        >
          {questionPoints(row.question)}
        </span>
      ) : (
        <Input
          type="number"
          inputMode="numeric"
          min="0"
          step="1"
          value={points ?? String(item.points)}
          readOnly={readOnly}
          aria-label={`Question ${row.label} points`}
          className={cn(
            'h-9 text-end text-sm tabular-nums md:text-sm',
            readOnly && readOnlyField
          )}
          onChange={(event) => {
            const value = event.target.value;
            setPoints(value);
            const parsed = Number(value);
            if (value.trim() && Number.isSafeInteger(parsed) && parsed >= 0)
              onChange({ points: parsed });
          }}
          onBlur={() => setPoints(null)}
        />
      )}
      {editing === 'none' || (editing === 'boxes' && !row.leaf) ? (
        <span />
      ) : (
        <DropdownMenu
          onOpenChange={(open) => {
            if (open) setHandingOff(false);
          }}
        >
          <DropdownMenuTrigger
            render={
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label={`Question ${row.label} actions`}
              />
            }
          >
            <EllipsisVertical aria-hidden="true" />
          </DropdownMenuTrigger>
          <DropdownMenuContent
            align="end"
            className="w-52"
            finalFocus={handingOff ? false : undefined}
          >
            {row.leaf && (
              <>
                <DropdownMenuItem onClick={onArm}>
                  <Crosshair aria-hidden="true" />
                  {row.leaf.boxes.length ? 'Draw another box' : 'Draw a box'}
                </DropdownMenuItem>
                <DropdownMenuItem
                  onClick={() => {
                    setHandingOff(true);
                    onPlace();
                  }}
                >
                  <Plus aria-hidden="true" />
                  Place a box to move with arrow keys
                </DropdownMenuItem>
              </>
            )}
            {editing === 'all' && (
              <>
                {!part && (
                  <DropdownMenuItem onClick={onAddPart}>
                    <Plus aria-hidden="true" />
                    Add subquestion
                  </DropdownMenuItem>
                )}
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={() => onMove(-1)}>
                  Move up
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => onMove(1)}>
                  Move down
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  variant="destructive"
                  onClick={() => {
                    setHandingOff(true);
                    onRemove();
                  }}
                >
                  {part ? 'Delete subquestion' : 'Delete question'}
                </DropdownMenuItem>
              </>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </li>
  );
}
