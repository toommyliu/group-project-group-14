import { useRef, useState } from 'react';
import { FileSpreadsheet, UserRound } from 'lucide-react';
import { RosterImportDialog } from '@/components/roster-import';
import { StudentDialog } from '@/components/student-dialog';
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';

type Step = 'closed' | 'choose' | 'one' | 'csv';

const choices = [
  { step: 'one', label: 'One student', Icon: UserRound },
  { step: 'csv', label: 'CSV file', Icon: FileSpreadsheet },
] as const;

export function AddStudents({
  onNotice,
}: {
  onNotice: (message: string) => void;
}) {
  const [step, setStep] = useState<Step>('closed');
  const [opening, setOpening] = useState(0);
  const trigger = useRef<HTMLButtonElement>(null);
  const close = () => setStep('closed');
  const returnFocus = () => trigger.current;
  const finish = (message: string) => {
    close();
    onNotice(message);
  };

  return (
    <>
      <Button
        ref={trigger}
        onClick={() => {
          onNotice('');
          setStep('choose');
        }}
      >
        Add students
      </Button>

      <AlertDialog
        open={step === 'choose'}
        onOpenChange={(open) => {
          if (!open) close();
        }}
      >
        <AlertDialogContent
          finalFocus={() => (step === 'closed' ? trigger.current : false)}
        >
          <AlertDialogHeader>
            <AlertDialogTitle>Add students</AlertDialogTitle>
          </AlertDialogHeader>
          <div className="grid grid-cols-2 gap-2">
            {choices.map(({ step: next, label, Icon }) => (
              <Button
                key={next}
                variant="outline"
                className="h-auto flex-col gap-3 py-6"
                onClick={() => {
                  if (next === 'one') setOpening((count) => count + 1);
                  setStep(next);
                }}
              >
                <Icon aria-hidden="true" className="size-8" />
                {label}
              </Button>
            ))}
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <StudentDialog
        key={opening}
        open={step === 'one'}
        student={null}
        onOpenChange={(open) => {
          if (!open) close();
        }}
        onDone={finish}
        finalFocus={returnFocus}
      />
      <RosterImportDialog
        open={step === 'csv'}
        onOpenChange={(open) => {
          if (!open) close();
        }}
        onImported={finish}
        finalFocus={returnFocus}
      />
    </>
  );
}
