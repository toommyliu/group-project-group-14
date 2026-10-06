import {
  useId,
  useRef,
  useState,
  type ComponentProps,
  type FormEvent,
  type RefObject,
} from 'react';
import { flushSync } from 'react-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ApiError } from '@/api/client';
import {
  addStudent,
  editStudent,
  removeStudent,
  rosterQuery,
  studentLabel,
  type Student,
  type StudentDetails,
} from '@/api/roster';
import { ErrorAlert } from '@/components/error-alert';
import { SteadyLabel } from '@/components/steady-label';
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
import { Button } from '@/components/ui/button';
import { Field, FieldError, FieldLabel } from '@/components/ui/field';
import { Input } from '@/components/ui/input';

type FieldName = 'first_name' | 'last_name' | 'sid' | 'email';
type Draft = Record<FieldName, string>;
type Errors = Partial<Record<FieldName, string>>;

const order: readonly FieldName[] = ['first_name', 'last_name', 'sid', 'email'];

function draftErrors(draft: Draft): Errors {
  return {
    ...(!draft.first_name.trim() &&
      !draft.last_name.trim() && { first_name: 'Enter a first or last name.' }),
    ...(!draft.sid.trim() && { sid: 'Enter an SID.' }),
  };
}

function StudentField({
  id,
  name,
  label,
  requirement,
  draft,
  errors,
  inputs,
  readOnly,
  onChange,
  ...input
}: {
  id: string;
  name: FieldName;
  label: string;
  requirement: 'required' | 'optional' | 'name';
  draft: Draft;
  errors: Errors;
  inputs: Record<FieldName, RefObject<HTMLInputElement | null>>;
  readOnly: boolean;
  onChange: (draft: Draft) => void;
} & Pick<
  ComponentProps<'input'>,
  'type' | 'inputMode' | 'maxLength' | 'placeholder'
>) {
  const error = errors[name];
  const described = [
    requirement === 'name' && `${id}-name-rule`,
    error && `${id}-${name}-error`,
  ]
    .filter(Boolean)
    .join(' ');
  return (
    <Field data-invalid={Boolean(error)}>
      <FieldLabel htmlFor={`${id}-${name}`}>
        {label}
        {requirement === 'optional' && (
          <span className="font-normal text-muted-foreground">(optional)</span>
        )}
      </FieldLabel>
      <Input
        ref={inputs[name]}
        id={`${id}-${name}`}
        name={name}
        value={draft[name]}
        onChange={(event) => onChange({ ...draft, [name]: event.target.value })}
        readOnly={readOnly}
        autoComplete="off"
        autoCapitalize={name === 'email' ? 'none' : undefined}
        spellCheck={false}
        aria-required={requirement === 'required' || undefined}
        aria-invalid={Boolean(error) || undefined}
        aria-describedby={described || undefined}
        {...input}
      />
      <FieldError id={`${id}-${name}-error`}>{error}</FieldError>
    </Field>
  );
}

export function RemoveStudentDialog({
  student,
  onClose,
  onRemoved,
  finalFocus,
}: {
  student: Student | null;
  onClose: () => void;
  onRemoved: (student: Student) => Promise<void>;
  finalFocus: () => HTMLElement | null;
}) {
  const cancel = useRef<HTMLButtonElement>(null);
  const [shown, setShown] = useState(student);
  if (student && student !== shown) setShown(student);
  const remove = useMutation({
    mutationFn: async (target: Student) => {
      try {
        await removeStudent(target.id);
      } catch (error) {
        if (!(error instanceof ApiError && error.status === 404)) throw error;
      }
      await onRemoved(target);
    },
  });
  return (
    <AlertDialog
      open={student !== null}
      onOpenChange={(next, details) => {
        if (remove.isPending) return details.cancel();
        if (!next) onClose();
      }}
      onOpenChangeComplete={(open) => {
        if (!open) remove.reset();
      }}
    >
      <AlertDialogContent initialFocus={cancel} finalFocus={finalFocus}>
        <AlertDialogHeader>
          <AlertDialogTitle className="wrap-anywhere">
            Remove {shown && studentLabel(shown)}?
          </AlertDialogTitle>
          <AlertDialogDescription>
            You can add them again later.
          </AlertDialogDescription>
        </AlertDialogHeader>
        {remove.isError && <ErrorAlert error={remove.error} />}
        <AlertDialogFooter>
          <AlertDialogCancel ref={cancel} disabled={remove.isPending}>
            Cancel
          </AlertDialogCancel>
          <AlertDialogAction
            type="button"
            variant="destructive"
            disabled={remove.isPending}
            onClick={() => student && remove.mutate(student)}
          >
            <SteadyLabel
              busy={remove.isPending}
              idle="Remove student"
              working="Removing…"
            />
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

/** `student` is null to add a student. Mount it with a new key for each opening. */
export function StudentDialog({
  open,
  student,
  onOpenChange,
  onDone,
  finalFocus,
}: {
  open: boolean;
  student: Student | null;
  onOpenChange: (open: boolean) => void;
  onDone: (message: string) => void;
  finalFocus: () => HTMLElement | null;
}) {
  const id = useId();
  const queryClient = useQueryClient();
  const [base, setBase] = useState(student);
  const [draft, setDraft] = useState<Draft>({
    first_name: student?.first_name ?? '',
    last_name: student?.last_name ?? '',
    sid: student?.sid ?? '',
    email: student?.email ?? '',
  });
  const [errors, setErrors] = useState<Errors>({});
  const [failure, setFailure] = useState<unknown>(null);
  const [notice, setNotice] = useState('');
  const [saving, setSaving] = useState(false);
  const firstName = useRef<HTMLInputElement>(null);
  const lastName = useRef<HTMLInputElement>(null);
  const sid = useRef<HTMLInputElement>(null);
  const email = useRef<HTMLInputElement>(null);
  const inputs: Record<FieldName, RefObject<HTMLInputElement | null>> = {
    first_name: firstName,
    last_name: lastName,
    sid,
    email,
  };

  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: rosterQuery.queryKey });

  /** Render the messages first so the focused field announces its error. */
  function showErrors(next: Errors) {
    flushSync(() => setErrors(next));
    const first = order.find((name) => next[name]);
    if (first) inputs[first].current?.focus();
    return first !== undefined;
  }

  async function recover(error: unknown) {
    if (error instanceof ApiError) {
      const fields: Errors = {};
      for (const name of order) fields[name] = error.fields[name];
      if (showErrors(fields)) return;
      if (base && error.status === 409) {
        const roster = await queryClient
          .fetchQuery({ ...rosterQuery, staleTime: 0 })
          .catch(() => []);
        const current = roster.find((item) => item.id === base.id);
        if (current) {
          setBase(current);
          setNotice(
            `${error.message} The saved details are now ${studentLabel(current)}` +
              `${current.email ? `, ${current.email}` : ''}. ` +
              'Save again to replace them with yours.'
          );
          return;
        }
      }
      if (error.status === 404) void refresh();
    }
    setFailure(error);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (saving) return;
    setFailure(null);
    setNotice('');
    if (showErrors(draftErrors(draft))) return;
    const details: StudentDetails = {
      sid: draft.sid.trim(),
      first_name: draft.first_name.trim(),
      last_name: draft.last_name.trim(),
      email: draft.email.trim() || null,
    };
    setSaving(true);
    try {
      const saved = base
        ? await editStudent(base, details)
        : await addStudent(details);
      await refresh();
      onDone(`${base ? 'Saved' : 'Added'} ${studentLabel(saved)}.`);
    } catch (error) {
      await recover(error);
    } finally {
      setSaving(false);
    }
  }

  const fieldProps = {
    id,
    draft,
    errors,
    inputs,
    readOnly: saving,
    onChange: (next: Draft) => {
      setDraft(next);
      setErrors({});
      setFailure(null);
    },
  };

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next, details) => {
        if (saving) return details.cancel();
        onOpenChange(next);
      }}
    >
      <AlertDialogContent
        initialFocus={firstName}
        finalFocus={finalFocus}
        className="max-h-[calc(100svh-2rem)] overflow-y-auto overscroll-contain"
      >
        <form
          noValidate
          onSubmit={(event) => void submit(event)}
          className="grid gap-4"
        >
          <AlertDialogHeader>
            <AlertDialogTitle>
              {base ? 'Edit student' : 'Add a student'}
            </AlertDialogTitle>
            <AlertDialogDescription id={`${id}-name-rule`}>
              Enter a first name, a last name, or both.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <StudentField
            {...fieldProps}
            name="first_name"
            label="First name"
            placeholder="Ana María"
            requirement="name"
            maxLength={200}
          />
          <StudentField
            {...fieldProps}
            name="last_name"
            label="Last name"
            placeholder="de la Cruz"
            requirement="name"
            maxLength={200}
          />
          <StudentField
            {...fieldProps}
            name="sid"
            label="SID"
            requirement="required"
            placeholder="012345678"
            maxLength={64}
          />
          <StudentField
            {...fieldProps}
            name="email"
            label="Email"
            requirement="optional"
            type="email"
            inputMode="email"
            placeholder="name@school.edu"
            maxLength={254}
          />
          <p role="status" className={notice ? 'text-sm' : 'sr-only'}>
            {notice}
          </p>
          {failure !== null && (
            <ErrorAlert
              title={
                base
                  ? 'The student could not be saved.'
                  : 'The student could not be added.'
              }
              error={failure}
            />
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={saving}>Cancel</AlertDialogCancel>
            <Button type="submit" disabled={saving}>
              <SteadyLabel
                busy={saving}
                idle={base ? 'Save changes' : 'Add student'}
                working={base ? 'Saving…' : 'Adding…'}
              />
            </Button>
          </AlertDialogFooter>
        </form>
      </AlertDialogContent>
    </AlertDialog>
  );
}
