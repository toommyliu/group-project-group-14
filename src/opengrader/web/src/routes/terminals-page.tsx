import { useRef, useState } from 'react';
import { CircleAlert, SquareTerminal } from 'lucide-react';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { terminalsRoute } from '@/router';
import {
  createTerminal,
  removeTerminal,
  renameTerminal,
  reorderTerminals,
  startTerminal,
  stopTerminal,
  terminalsQuery,
  type TerminalSession,
} from '@/api/terminals';
import { TerminalTabs } from '@/components/terminal-tabs';
import { Tabs, TabsContent } from '@/components/ui/tabs';
import { RemoteTerminal } from '@/components/remote-terminal';
import { Button } from '@/components/ui/button';
import { ErrorAlert } from '@/components/error-alert';
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from '@/components/ui/empty';
import { PageHeading } from '@/components/page-heading';

export default function TerminalsPage() {
  const { assignment: assignmentData } = terminalsRoute.useLoaderData();
  const assignment = assignmentData.id;
  const queryClient = useQueryClient();
  const query = terminalsQuery(assignment);
  const { data: catalog, error } = useQuery(query);
  const sessions = catalog?.sessions ?? [];
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<{
    kind: 'stop' | 'close';
    session: TerminalSession;
  } | null>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const newTerminalRef = useRef<HTMLButtonElement>(null);
  const creatingRef = useRef(false);
  const selected =
    sessions.find((session) => session.id === selectedId) ?? sessions[0];
  const selectedFailed =
    selected?.status === 'stopped' &&
    selected.message.startsWith('Could not start:');
  const selectedFailureMessage = selectedFailed
    ? selected.message.slice('Could not start:'.length).trim() ||
      'Check the server setup, then try again.'
    : null;
  const [renamingId, setRenamingId] = useState<string | null>(null);
  function beginRename(id: string) {
    if (action.isPending) return;
    setSelectedId(id);
    setRenamingId(id);
  }
  const action = useMutation({
    mutationFn: async (run: () => Promise<unknown>) => run(),
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: query.queryKey }),
  });
  function start(session: TerminalSession) {
    action.reset();
    setSelectedId(session.id);
    setRenamingId(null);
    action.mutate(() => startTerminal(session.id));
  }
  function confirm(kind: 'stop' | 'close', session: TerminalSession) {
    action.reset();
    setSelectedId(session.id);
    setRenamingId(null);
    setConfirmation({ kind, session });
  }
  function create() {
    if (
      creatingRef.current ||
      action.isPending ||
      !catalog ||
      sessions.length >= catalog.max_sessions
    )
      return;
    creatingRef.current = true;
    action.reset();
    action.mutate(
      async () => {
        const session = await createTerminal(assignment);
        setSelectedId(session.id);
        try {
          await startTerminal(session.id);
        } catch (cause) {
          await removeTerminal(session.id).catch(() => undefined);
          throw cause;
        }
      },
      {
        onSettled: () => {
          creatingRef.current = false;
        },
      }
    );
  }
  const limitReached = !catalog || sessions.length >= catalog.max_sessions;
  const hasError = !confirmation && Boolean(error || action.error);
  return (
    <>
      <PageHeading
        title="Terminals"
        description={assignmentData.title}
        className={hasError ? 'mb-6' : undefined}
      />
      {hasError && (
        <ErrorAlert
          className="mb-6"
          title={
            action.error ? 'Terminal action failed' : 'Could not load terminals'
          }
          error={action.error ?? error}
        />
      )}
      <section
        className="flex h-[calc(100svh-10rem)] min-h-96 flex-col overflow-hidden border"
        aria-label="Terminal sessions"
      >
        <Tabs
          value={selected?.id ?? null}
          onValueChange={(value) => {
            setSelectedId(String(value));
            setRenamingId(null);
          }}
          className="flex min-h-0 min-w-0 flex-1 flex-col gap-0"
        >
          {sessions.length > 0 && (
            <TerminalTabs
              sessions={sessions}
              disabled={action.isPending}
              renamingId={renamingId}
              onRename={beginRename}
              onSave={async (id, title) => {
                await action.mutateAsync(() => renameTerminal(id, title));
                setRenamingId(null);
              }}
              onCancel={() => setRenamingId(null)}
              onStart={start}
              onStop={(session) => confirm('stop', session)}
              onClose={(session) => confirm('close', session)}
              onMove={(ids) =>
                action.mutate(async () => {
                  const ordered = await reorderTerminals(assignment, ids);
                  queryClient.setQueryData(query.queryKey, (current) =>
                    current ? { ...current, sessions: ordered } : current
                  );
                })
              }
              createDisabled={action.isPending || limitReached}
              createButtonRef={newTerminalRef}
              onCreate={create}
            />
          )}
          {selected ? (
            <TabsContent
              value={selected.id}
              className="flex min-h-0 flex-1 flex-col pt-0"
            >
              {selected.status === 'running' ? (
                <RemoteTerminal key={selected.id} id={selected.id} />
              ) : (
                <Empty>
                  <EmptyHeader>
                    <EmptyMedia variant="icon">
                      {selectedFailed ? (
                        <CircleAlert
                          className="text-destructive"
                          aria-hidden="true"
                        />
                      ) : (
                        <SquareTerminal aria-hidden="true" />
                      )}
                    </EmptyMedia>
                    <EmptyTitle>
                      {selectedFailed
                        ? 'Terminal failed to start'
                        : 'Terminal stopped'}
                    </EmptyTitle>
                    <EmptyDescription>
                      {selectedFailed
                        ? selectedFailureMessage
                        : 'Working directory and scrollback are saved.'}
                    </EmptyDescription>
                  </EmptyHeader>
                  <EmptyContent>
                    <Button
                      disabled={action.isPending}
                      onClick={() => start(selected)}
                    >
                      Start session
                    </Button>
                  </EmptyContent>
                </Empty>
              )}
            </TabsContent>
          ) : (
            <Empty>
              <EmptyHeader>
                <EmptyMedia variant="icon">
                  <SquareTerminal aria-hidden="true" />
                </EmptyMedia>
                <EmptyTitle>No terminals yet</EmptyTitle>
                <EmptyDescription>
                  Create a terminal to run commands for this assignment.
                </EmptyDescription>
              </EmptyHeader>
              <EmptyContent>
                <Button
                  ref={newTerminalRef}
                  disabled={action.isPending || limitReached}
                  onClick={create}
                >
                  New terminal
                </Button>
              </EmptyContent>
            </Empty>
          )}
        </Tabs>
        <p className="border-t px-4 py-2 text-xs text-muted-foreground">
          Closing this page disconnects. Idle shells stop after 30 minutes.
          Commands with running jobs stay alive.
        </p>
        <AlertDialog
          open={confirmation !== null}
          onOpenChange={(open) => {
            if (!open && !action.isPending) setConfirmation(null);
          }}
        >
          <AlertDialogContent
            initialFocus={cancelRef}
            finalFocus={newTerminalRef}
          >
            <AlertDialogHeader>
              <AlertDialogTitle>
                {confirmation?.kind === 'stop'
                  ? 'Stop session?'
                  : 'Close terminal?'}
              </AlertDialogTitle>
              <AlertDialogDescription>
                {confirmation?.kind === 'stop'
                  ? 'Running commands in this terminal will end. Files already saved will remain.'
                  : confirmation?.session.status === 'running'
                    ? 'Running commands will end. This also removes the saved terminal and its scrollback. Files on the server will remain.'
                    : 'This removes the saved terminal and its scrollback. Files on the server will remain.'}
              </AlertDialogDescription>
            </AlertDialogHeader>
            {action.error && (
              <ErrorAlert
                title={
                  confirmation?.kind === 'stop'
                    ? 'Could not stop session'
                    : 'Could not close terminal'
                }
                error={action.error}
              />
            )}
            <AlertDialogFooter>
              <AlertDialogCancel ref={cancelRef} disabled={action.isPending}>
                Cancel
              </AlertDialogCancel>
              <AlertDialogAction
                variant="destructive"
                disabled={action.isPending}
                onClick={() => {
                  if (!confirmation) return;
                  const target = confirmation;
                  action.mutate(async () => {
                    if (target.kind === 'stop') {
                      await stopTerminal(target.session.id);
                    } else {
                      if (target.session.status === 'running')
                        await stopTerminal(target.session.id);
                      await removeTerminal(target.session.id);
                    }
                    setConfirmation(null);
                  });
                }}
              >
                {action.isPending
                  ? 'Working…'
                  : confirmation?.kind === 'stop'
                    ? 'Stop session'
                    : 'Close terminal'}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </section>
    </>
  );
}
