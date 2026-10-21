import { useId, useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Copy, LoaderCircle } from 'lucide-react';
import {
  acceptFix,
  assistantQuery,
  deleteCopy,
  dismissFix,
  makeCopy,
  makeKeyFolder,
  mergeCopy,
  mergePreviewQuery,
  storeKeyFolder,
  suggestionsQuery,
  type AssistantCopy,
  type AssistantOverview,
  type KeyFileChoice,
} from '@/api/assistant';
import { ErrorAlert } from '@/components/error-alert';
import { PageHeading } from '@/components/page-heading';
import { SteadyLabel } from '@/components/steady-label';
import { Button } from '@/components/ui/button';
import { assistantRoute } from '@/router';

/** Copy text, and say so on the button for a moment. */
function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      variant="outline"
      size="sm"
      onClick={() => {
        void navigator.clipboard.writeText(text).then(() => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1500);
        });
      }}
    >
      {copied ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
      {copied ? 'Copied' : label}
    </Button>
  );
}

function Busy({
  busy,
  idle,
  working,
}: {
  busy: boolean;
  idle: string;
  working: string;
}) {
  return (
    <SteadyLabel
      busy={busy}
      idle={idle}
      working={
        <>
          <LoaderCircle
            aria-hidden="true"
            className="motion-safe:animate-spin"
          />
          {working}
        </>
      }
    />
  );
}

function Lines({ lines }: { lines: string[] }) {
  return (
    <div role="status" className="mt-3 space-y-1 text-sm whitespace-pre-wrap">
      {lines.filter(Boolean).map((line, index) => (
        <p key={index}>{line}</p>
      ))}
    </div>
  );
}

/** Where to start each kind of assistant so that it works in `folder`. */
function OpenInstructions({
  folder,
  prompt,
}: {
  folder: string;
  prompt: string;
}) {
  const quoted = `'${folder.replaceAll("'", "'\\''")}'`;
  return (
    <div className="mt-3 space-y-3 text-sm">
      <p>
        Open your AI assistant <strong>in this folder</strong>, never in the
        workspace:
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <code className="min-w-0 rounded-sm bg-muted px-2 py-1 [overflow-wrap:anywhere]">
          {folder}
        </code>
        <CopyButton text={folder} label="Copy path" />
      </div>
      <pre className="overflow-x-auto rounded-sm bg-muted px-3 py-2 text-xs">
        {`Claude Code:  cd ${quoted} && claude\nCodex:        cd ${quoted} && codex\nGemini CLI:   cd ${quoted} && gemini`}
      </pre>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-muted-foreground">Then say:</span>
        <span className="min-w-0 flex-1">{prompt}</span>
        <CopyButton text={prompt} label="Copy prompt" />
      </div>
    </div>
  );
}

/**
 * Whether a change to the answer key goes into the assignment's qa.md too.
 * Left to the teacher, with no default: writing grades to files never
 * carries the key, so the two would otherwise part ways unnoticed.
 */
function KeyFileChoiceField({
  keyFile,
  value,
  onChange,
}: {
  keyFile: string;
  value: KeyFileChoice | null;
  onChange: (value: KeyFileChoice) => void;
}) {
  const name = useId();
  const options: [KeyFileChoice, ReactNode][] = [
    ['update', 'Update qa.md too'],
    ['leave', 'Workspace only'],
  ];
  return (
    <fieldset className="mt-3 text-sm">
      <legend className="text-muted-foreground">
        This answer key also lives in{' '}
        <code className="[overflow-wrap:anywhere]">{keyFile}</code>. Should an
        accepted change go there too?
      </legend>
      <div className="mt-2 flex flex-wrap gap-4">
        {options.map(([option, label]) => (
          <label key={option} className="inline-flex items-center gap-2">
            <input
              type="radio"
              name={name}
              checked={value === option}
              onChange={() => onChange(option)}
            />
            {label}
          </label>
        ))}
      </div>
    </fieldset>
  );
}

function Suggestions({
  assignmentId,
  copy,
  overview,
  keyFile,
  setKeyFile,
}: {
  assignmentId: string;
  copy: AssistantCopy;
  overview: AssistantOverview;
  keyFile: KeyFileChoice | null;
  setKeyFile: (value: KeyFileChoice) => void;
}) {
  const queryClient = useQueryClient();
  const query = useQuery(suggestionsQuery(assignmentId, copy.name));
  const [lines, setLines] = useState<string[]>([]);
  const decide = useMutation({
    mutationFn: ({ id, accept }: { id: string; accept: boolean }) =>
      accept
        ? acceptFix(assignmentId, copy.name, id, keyFile)
        : dismissFix(assignmentId, copy.name, id),
    onSuccess: async (result) => {
      setLines(result.lines);
      await queryClient.invalidateQueries({
        queryKey: ['assignments', assignmentId],
      });
    },
  });
  const choiceNeeded = Boolean(overview.key_file) && keyFile === null;
  if (query.isError)
    return (
      <ErrorAlert
        title="The suggestions could not be read."
        error={query.error}
      />
    );
  const found = query.data ?? [];
  return (
    <div className="mt-4 border-t pt-4">
      <h4 className="text-sm font-semibold">
        Fixes it suggested to the answer key
      </h4>
      {found.length === 0 ? (
        <p className="mt-1 text-sm text-muted-foreground">None yet.</p>
      ) : (
        <>
          {overview.key_file && (
            <KeyFileChoiceField
              keyFile={overview.key_file}
              value={keyFile}
              onChange={setKeyFile}
            />
          )}
          <ul className="mt-3 space-y-3">
            {found.map((item) => (
              <li key={item.id} className="text-sm">
                <p>
                  <strong>{item.id}</strong> {item.kind} · {item.question}
                  {item.version && ` (version ${item.version})`}: {item.message}
                </p>
                {item.fix && (
                  <p className="text-muted-foreground">{item.fix}</p>
                )}
                {item.decision ? (
                  <p className="text-muted-foreground">{item.decision}</p>
                ) : (
                  <div className="mt-1 flex gap-2">
                    <Button
                      size="sm"
                      disabled={
                        !item.fixable || choiceNeeded || decide.isPending
                      }
                      title={
                        !item.fixable
                          ? 'No fix to apply: read it and change the key yourself.'
                          : choiceNeeded
                            ? 'Choose above whether qa.md follows first.'
                            : undefined
                      }
                      onClick={() =>
                        decide.mutate({ id: item.id, accept: true })
                      }
                    >
                      Accept
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={decide.isPending}
                      onClick={() =>
                        decide.mutate({ id: item.id, accept: false })
                      }
                    >
                      Dismiss
                    </Button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
      {decide.isError && (
        <ErrorAlert
          className="mt-3"
          title="Not applied."
          error={decide.error}
        />
      )}
      <Lines lines={lines} />
    </div>
  );
}

function CopyCard({
  assignmentId,
  copy,
  overview,
  keyFile,
  setKeyFile,
}: {
  assignmentId: string;
  copy: AssistantCopy;
  overview: AssistantOverview;
  keyFile: KeyFileChoice | null;
  setKeyFile: (value: KeyFileChoice) => void;
}) {
  const queryClient = useQueryClient();
  const rubrics = copy.purpose === 'rubrics';
  const [previewing, setPreviewing] = useState(false);
  const [armed, setArmed] = useState(false);
  const preview = useQuery({
    ...mergePreviewQuery(assignmentId, copy.name),
    enabled: previewing,
  });
  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: ['assignments', assignmentId] });
  const merge = useMutation({
    mutationFn: () => mergeCopy(assignmentId, copy.name),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: () => deleteCopy(assignmentId, copy.name),
    onSuccess: refresh,
  });
  return (
    <section className="rounded-sm border p-4">
      <h3 className="font-semibold [overflow-wrap:anywhere]">
        {copy.name}{' '}
        <span className="font-normal text-muted-foreground">
          · {rubrics ? 'drafting rubrics' : 'grading'}
        </span>
      </h3>
      <p className="mt-1 text-sm text-muted-foreground">
        {rubrics
          ? `${copy.students} student(s); it grades nothing, and its drafts arrive as suggestions below.`
          : `${copy.students} student(s): ${copy.ungraded} answer(s) still to grade, ${copy.graded_by_agent} graded by your assistant.`}
      </p>
      <OpenInstructions
        folder={copy.path}
        prompt={rubrics ? overview.prompts.rubrics : overview.prompts.grading}
      />
      <details className="mt-3 text-sm">
        <summary className="cursor-pointer">
          Claude Desktop, ChatGPT or another chat app (MCP)
        </summary>
        <p className="mt-2 text-muted-foreground">
          The safest way: the assistant then has no file access at all. Paste
          the part for your app.
        </p>
        <pre className="mt-2 max-h-64 overflow-auto rounded-sm bg-muted px-3 py-2 text-xs whitespace-pre-wrap">
          {copy.mcp_setup}
        </pre>
        <CopyButton text={copy.mcp_setup} label="Copy MCP setup" />
      </details>
      <div className="mt-4 flex flex-wrap gap-2">
        {!rubrics && (
          <Button variant="outline" onClick={() => setPreviewing(true)}>
            Preview merge
          </Button>
        )}
        <Button
          variant="destructive"
          disabled={remove.isPending}
          onClick={() => (armed ? remove.mutate() : setArmed(true))}
          onBlur={() => setArmed(false)}
        >
          {armed ? 'Really delete the copy?' : 'Delete copy'}
        </Button>
      </div>
      {remove.isError && (
        <ErrorAlert
          className="mt-3"
          title="The copy was not deleted."
          error={remove.error}
        />
      )}
      {previewing && !rubrics && (
        <div className="mt-4 border-t pt-4">
          <h4 className="text-sm font-semibold">Merge it back</h4>
          <p className="mt-1 text-sm text-muted-foreground">
            Its grades are saved as AI grades for you to confirm while grading.
            An answer graded here since the copy was made is never overwritten:
            the assistant&apos;s grade waits as a change request.
          </p>
          {preview.isPending ? (
            <p className="mt-3 text-sm">
              Comparing the copy with the workspace…
            </p>
          ) : preview.isError ? (
            <ErrorAlert
              className="mt-3"
              title="Nothing can be merged."
              error={preview.error}
            />
          ) : merge.isSuccess ? (
            <Lines lines={merge.data.lines} />
          ) : (
            <>
              <Lines lines={preview.data.lines} />
              {preview.data.grades > 0 && (
                <Button
                  className="mt-3"
                  disabled={merge.isPending}
                  onClick={() => merge.mutate()}
                >
                  <Busy
                    busy={merge.isPending}
                    idle="Merge"
                    working="Merging…"
                  />
                </Button>
              )}
            </>
          )}
          {merge.isError && (
            <ErrorAlert
              className="mt-3"
              title="Nothing was merged."
              error={merge.error}
            />
          )}
        </div>
      )}
      <Suggestions
        assignmentId={assignmentId}
        copy={copy}
        overview={overview}
        keyFile={keyFile}
        setKeyFile={setKeyFile}
      />
    </section>
  );
}

function AnswerKeySection({
  assignmentId,
  overview,
  keyFile,
  setKeyFile,
}: {
  assignmentId: string;
  overview: AssistantOverview;
  keyFile: KeyFileChoice | null;
  setKeyFile: (value: KeyFileChoice) => void;
}) {
  const queryClient = useQueryClient();
  const folder = overview.key_folders[0];
  const [replace, setReplace] = useState(false);
  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: ['assignments', assignmentId] });
  const make = useMutation({
    mutationFn: () => makeKeyFolder(assignmentId),
    onSuccess: refresh,
  });
  const store = useMutation({
    mutationFn: () =>
      storeKeyFolder(assignmentId, folder.name, keyFile, replace),
    onSuccess: async () => {
      setReplace(false);
      await refresh();
    },
    onError: (error) => {
      // The key changed since the folder was made: a second press stores it anyway.
      if (error.message.includes('on purpose')) setReplace(true);
    },
  });
  const choiceNeeded = Boolean(overview.key_file) && keyFile === null;
  return (
    <section className="rounded-sm border p-4">
      <h2 className="font-semibold">The answer key</h2>
      <p className="mt-1 text-sm text-muted-foreground">
        {overview.unanswered > 0
          ? `${overview.unanswered} question(s) have no answer yet. `
          : ''}
        Your assistant fills or corrects the key in its own folder beside the
        workspace, with no student work in it, and checks it before you store
        it.
      </p>
      {folder ? (
        <>
          <OpenInstructions
            folder={folder.path}
            prompt={overview.prompts.key}
          />
          {overview.key_file && (
            <KeyFileChoiceField
              keyFile={overview.key_file}
              value={keyFile}
              onChange={setKeyFile}
            />
          )}
          <Button
            className="mt-4"
            disabled={!folder.has_key || choiceNeeded || store.isPending}
            onClick={() => store.mutate()}
          >
            <Busy
              busy={store.isPending}
              idle={replace ? 'Store it anyway' : 'Store the key it wrote'}
              working="Storing…"
            />
          </Button>
          {store.isError && (
            <ErrorAlert
              className="mt-3"
              title="The key was not stored."
              error={store.error}
            />
          )}
          {store.isSuccess && <Lines lines={store.data.lines} />}
        </>
      ) : (
        <Button
          className="mt-4"
          disabled={make.isPending}
          onClick={() => make.mutate()}
        >
          <Busy
            busy={make.isPending}
            idle="Give the key to my assistant"
            working="Making its folder…"
          />
        </Button>
      )}
      {make.isError && (
        <ErrorAlert
          className="mt-3"
          title="Nothing was given to the assistant."
          error={make.error}
        />
      )}
    </section>
  );
}

export default function AssistantPage() {
  const { assignment, overview: initial } = assistantRoute.useLoaderData();
  const queryClient = useQueryClient();
  const { data: overview } = useQuery({
    ...assistantQuery(assignment.id),
    initialData: initial,
  });
  // Asked once for the page, on the first change to the key.
  const [keyFile, setKeyFile] = useState<KeyFileChoice | null>(null);
  const make = useMutation({
    mutationFn: (purpose: 'grading' | 'rubrics') =>
      makeCopy(assignment.id, purpose),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: assistantQuery(assignment.id).queryKey,
      }),
  });
  return (
    <>
      <PageHeading
        title="AI assistant"
        description="Your own assistant (Claude, ChatGPT, Codex, …) works in a copy beside the workspace with no names, IDs or scans. Nothing it does reaches your grades until you merge it back."
      />
      <div className="grid max-w-3xl gap-6">
        <p className="text-sm">
          <strong>{overview.title}</strong> · {overview.submission_count}{' '}
          submission(s)
          {overview.rubric_gaps > 0 &&
            ` · ${overview.rubric_gaps} written question(s) without partial credit`}
          . Copies go to{' '}
          <code className="[overflow-wrap:anywhere]">
            {overview.output_dir}
          </code>
          . Use the account your school approved if it has one; on a personal
          plan, turn off training on your data first.
        </p>
        <div className="flex flex-wrap gap-2">
          {overview.can_grade && (
            <Button
              disabled={make.isPending}
              onClick={() => make.mutate('grading')}
            >
              <Busy
                busy={make.isPending && make.variables === 'grading'}
                idle="Make a copy for grading"
                working="Checking every answer image…"
              />
            </Button>
          )}
          {overview.can_draft_rubrics && (
            <Button
              variant="outline"
              disabled={make.isPending}
              onClick={() => make.mutate('rubrics')}
            >
              <Busy
                busy={make.isPending && make.variables === 'rubrics'}
                idle="Make a copy for drafting rubrics"
                working="Checking every answer image…"
              />
            </Button>
          )}
        </div>
        {make.isError && (
          <ErrorAlert title="No copy was made." error={make.error} />
        )}
        {overview.copies.map((copy) => (
          <CopyCard
            key={copy.name}
            assignmentId={assignment.id}
            copy={copy}
            overview={overview}
            keyFile={keyFile}
            setKeyFile={setKeyFile}
          />
        ))}
        <AnswerKeySection
          assignmentId={assignment.id}
          overview={overview}
          keyFile={keyFile}
          setKeyFile={setKeyFile}
        />
      </div>
    </>
  );
}
