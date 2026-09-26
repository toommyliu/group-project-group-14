import { useState, type ReactNode } from 'react';
import { Check, Copy } from 'lucide-react';
import assignment from '@/lib/qa-format/assignment.md?raw';
import question from '@/lib/qa-format/question.md?raw';
import multipleChoice from '@/lib/qa-format/multiple-choice.md?raw';
import parts from '@/lib/qa-format/parts.md?raw';
import rubric from '@/lib/qa-format/rubric.md?raw';
import versions from '@/lib/qa-format/versions.md?raw';
import { Button } from './ui/button';

function Code({ children }: { children: ReactNode }) {
  return (
    <code className="rounded-sm bg-muted px-1 py-0.5 font-mono text-xs">
      {children}
    </code>
  );
}

const sections: { title: string; description: ReactNode; example: string }[] = [
  {
    title: 'Assignment',
    description: (
      <>
        Start with the title between two <Code>---</Code> lines. Instructions
        are optional.
      </>
    ),
    example: assignment,
  },
  {
    title: 'Question',
    description: (
      <>
        Each top-level bullet is a question. Put its points in parentheses and
        list every accepted answer under <Code>**Correct**</Code>.
      </>
    ),
    example: question,
  },
  {
    title: 'Multiple choice',
    description: (
      <>
        Label choices A, B, C in order under <Code>**Choices**</Code>. List each
        correct label under <Code>**Correct**</Code>.
      </>
    ),
    example: multipleChoice,
  },
  {
    title: 'Parts',
    description:
      'Indent questions under a question to split it into parts with their own points.',
    example: parts,
  },
  {
    title: 'Rubric',
    description: (
      <>
        Use exactly one Correct, Partially Correct, and Incorrect row. Award
        points from zero with <Code>+</Code> values, or deduct from full credit
        with <Code>-</Code> values. Don’t mix the two.
      </>
    ),
    example: rubric,
  },
  {
    title: 'Versions',
    description: (
      <>
        Put each version’s questions under its own <Code>##</Code> heading.
      </>
    ),
    example: versions,
  },
];

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      aria-label={copied ? `Copied ${label} example` : `Copy ${label} example`}
      onClick={() => {
        void navigator.clipboard.writeText(text).then(() => {
          setCopied(true);
          globalThis.setTimeout(() => setCopied(false), 1500);
        });
      }}
    >
      {copied ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
      {copied ? 'Copied' : 'Copy'}
    </Button>
  );
}

export function QAFormatGuide() {
  return (
    <div className="space-y-8">
      {sections.map((section) => (
        <section key={section.title} className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <h3 className="font-medium">{section.title}</h3>
            <CopyButton text={section.example} label={section.title} />
          </div>
          <p className="text-muted-foreground">{section.description}</p>
          <pre className="overflow-x-auto rounded-md border bg-muted/50 p-3 font-mono text-xs leading-relaxed">
            {section.example.trimEnd()}
          </pre>
        </section>
      ))}
    </div>
  );
}
