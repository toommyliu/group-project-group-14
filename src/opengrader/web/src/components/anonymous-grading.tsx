import { useId } from 'react';
import { setPreference, usePreferences } from '@/stores/preferences';
import { Label } from './ui/label';
import { Switch } from './ui/switch';

export function AnonymousGrading() {
  const id = useId();
  const enabled = usePreferences((state) => state.anonymousGrading);
  return (
    <Label
      htmlFor={id}
      className="flex min-h-11 cursor-pointer items-center gap-2 px-2"
      title="Hide names and student IDs in grading and review. Submissions and downloaded files keep student details."
    >
      <Switch
        id={id}
        checked={enabled}
        onCheckedChange={(checked) =>
          setPreference('anonymousGrading', checked)
        }
      />
      Anonymous grading
    </Label>
  );
}
