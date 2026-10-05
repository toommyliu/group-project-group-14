import type { Ref } from 'react';
import { terms, termName } from '@/api/workspace';
import type { CourseDraft, CourseDraftErrors } from '@/lib/course-draft';
import { Field, FieldError, FieldLabel } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';

function yearOptions(selected: number | null) {
  const latest = new Date().getFullYear() + 2;
  const years = Array.from({ length: 13 }, (_, index) => latest - index);
  return selected === null || years.includes(selected)
    ? years
    : [...years, selected].sort((a, b) => b - a);
}

export function CourseFields({
  draft,
  onChange,
  errors,
  readOnly,
  numberInput,
  nameInput,
}: {
  draft: CourseDraft;
  onChange: (draft: CourseDraft) => void;
  errors: CourseDraftErrors;
  readOnly: boolean;
  numberInput?: Ref<HTMLInputElement>;
  nameInput?: Ref<HTMLInputElement>;
}) {
  const termItems = terms.map((term) => ({
    value: term,
    label: termName(term),
  }));
  const yearItems = yearOptions(draft.year).map((year) => ({
    value: year,
    label: String(year),
  }));
  return (
    <>
      <Field data-invalid={Boolean(errors.number)}>
        <FieldLabel htmlFor="course-number">Course number</FieldLabel>
        <Input
          ref={numberInput}
          id="course-number"
          name="course-number"
          value={draft.number}
          onChange={(event) =>
            onChange({ ...draft, number: event.target.value })
          }
          aria-required="true"
          readOnly={readOnly}
          maxLength={120}
          placeholder="CMPE 195A"
          aria-invalid={Boolean(errors.number) || undefined}
          aria-describedby={errors.number ? 'course-number-error' : undefined}
        />
        <FieldError id="course-number-error">{errors.number}</FieldError>
      </Field>
      <Field data-invalid={Boolean(errors.name)}>
        <FieldLabel htmlFor="course-name">Course name</FieldLabel>
        <Input
          ref={nameInput}
          id="course-name"
          name="course-name"
          value={draft.name}
          onChange={(event) => onChange({ ...draft, name: event.target.value })}
          aria-required="true"
          readOnly={readOnly}
          maxLength={120}
          placeholder="Senior Design Project I"
          aria-invalid={Boolean(errors.name) || undefined}
          aria-describedby={errors.name ? 'course-name-error' : undefined}
        />
        <FieldError id="course-name-error">{errors.name}</FieldError>
      </Field>
      <div className="grid grid-cols-2 gap-4">
        <Field>
          <FieldLabel htmlFor="course-term">
            Term
            <span className="font-normal text-muted-foreground">
              (optional)
            </span>
          </FieldLabel>
          <Select
            items={termItems}
            value={draft.term}
            disabled={readOnly}
            onValueChange={(term) => onChange({ ...draft, term })}
          >
            <SelectTrigger id="course-term" className="w-full">
              <SelectValue placeholder="Select term" />
            </SelectTrigger>
            <SelectContent align="start">
              {termItems.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        <Field>
          <FieldLabel htmlFor="course-year">
            Year
            <span className="font-normal text-muted-foreground">
              (optional)
            </span>
          </FieldLabel>
          <Select
            items={yearItems}
            value={draft.year}
            disabled={readOnly}
            onValueChange={(year) => onChange({ ...draft, year })}
          >
            <SelectTrigger id="course-year" className="w-full">
              <SelectValue placeholder="Select year" />
            </SelectTrigger>
            <SelectContent align="start">
              {yearItems.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
      </div>
    </>
  );
}
