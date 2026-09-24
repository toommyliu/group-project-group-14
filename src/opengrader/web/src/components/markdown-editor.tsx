import { defaultKeymap, history, historyKeymap } from '@codemirror/commands';
import {
  defaultHighlightStyle,
  syntaxHighlighting,
} from '@codemirror/language';
import { search, searchKeymap } from '@codemirror/search';
import { Annotation, Compartment, EditorState } from '@codemirror/state';
import {
  EditorView,
  drawSelection,
  highlightActiveLine,
  highlightActiveLineGutter,
  highlightSpecialChars,
  keymap,
  lineNumbers,
  placeholder as editorPlaceholder,
} from '@codemirror/view';
import {
  memo,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type Ref,
} from 'react';
import type { OutlineEntry } from '@/lib/assignment-outline';
import { cn } from '@/lib/utils';

export type MarkdownEditorNavigation = {
  focusLine: (line: number) => void;
};

const externalDocument = Annotation.define<boolean>();

const editorTheme = EditorView.theme({
  '&': {
    height: '100%',
    minHeight: '0',
    backgroundColor: 'var(--background)',
    color: 'var(--foreground)',
    fontSize: '1rem',
  },
  '&.cm-focused': {
    outline: '2px solid var(--ring)',
    outlineOffset: '-1px',
  },
  '.cm-scroller': {
    overflow: 'auto',
    overscrollBehavior: 'contain',
    fontFamily:
      'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace',
    lineHeight: '1.625',
  },
  '.cm-content': {
    minHeight: '100%',
    padding: '0',
    caretColor: 'var(--foreground)',
  },
  '.cm-line': { padding: '0 1rem 0 0.75rem' },
  '.cm-gutters': {
    backgroundColor: 'color-mix(in oklab, var(--muted) 45%, transparent)',
    color: 'var(--muted-foreground)',
    borderRight: '1px solid var(--border)',
  },
  '.cm-activeLine, .cm-activeLineGutter': {
    backgroundColor: 'color-mix(in oklab, var(--accent) 65%, transparent)',
  },
  '.cm-selectionBackground, &.cm-focused .cm-selectionBackground': {
    backgroundColor: 'color-mix(in oklab, var(--primary) 25%, transparent)',
  },
  '.cm-placeholder': { color: 'var(--muted-foreground)' },
});

function entryAtLine(entries: OutlineEntry[], line: number) {
  let low = 0;
  let high = entries.length;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if ((entries[middle].line ?? Number.POSITIVE_INFINITY) <= line)
      low = middle + 1;
    else high = middle;
  }
  return entries[Math.max(0, low - 1)];
}

export const MarkdownEditor = memo(function MarkdownEditor({
  ref,
  id,
  value,
  readOnly,
  disabled,
  invalid,
  describedBy,
  outlineEntries,
  active,
  navigation,
  className,
  placeholder,
  onChange,
  onBlur,
  onActiveChange,
}: {
  ref?: Ref<MarkdownEditorNavigation>;
  id: string;
  value: string;
  readOnly: boolean;
  disabled: boolean;
  invalid: boolean;
  describedBy?: string;
  outlineEntries: OutlineEntry[];
  active: boolean;
  navigation?: { line: number };
  className?: string;
  placeholder: string;
  onChange: (value: string) => void;
  onBlur: () => void;
  onActiveChange: (key: string | null) => void;
}) {
  const mountRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView>(null);
  const callbacks = useRef({ onChange, onBlur, onActiveChange });
  const entries = useRef(outlineEntries);
  const isActive = useRef(active);
  const lastActiveKey = useRef<string | null>(null);
  callbacks.current = { onChange, onBlur, onActiveChange };
  const sortedEntries = useMemo(
    () =>
      outlineEntries
        .filter((entry) => entry.line !== null)
        .toSorted((a, b) => (a.line ?? 0) - (b.line ?? 0)),
    [outlineEntries]
  );
  entries.current = sortedEntries;
  isActive.current = active;

  const [editable] = useState(() => new Compartment());
  const [attributes] = useState(() => new Compartment());

  function publishActive(view: EditorView, fromCursor: boolean) {
    if (!isActive.current || !entries.current.length) return;
    const position = fromCursor
      ? view.state.selection.main.head
      : view.lineBlockAtHeight(
          view.scrollDOM.scrollTop + view.scrollDOM.clientHeight / 3
        ).from;
    const line = view.state.doc.lineAt(position).number;
    const entry = entryAtLine(entries.current, line);
    const key = entry?.key ?? null;
    if (key !== lastActiveKey.current) {
      lastActiveKey.current = key;
      callbacks.current.onActiveChange(key);
    }
  }

  useEffect(() => {
    const parent = mountRef.current;
    if (!parent) return;
    const view = new EditorView({
      parent,
      doc: value,
      extensions: [
        highlightSpecialChars(),
        history(),
        drawSelection(),
        syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
        search({ top: true }),
        keymap.of([...defaultKeymap, ...historyKeymap, ...searchKeymap]),
        lineNumbers(),
        highlightActiveLine(),
        highlightActiveLineGutter(),
        EditorView.lineWrapping,
        editorTheme,
        editorPlaceholder(placeholder),
        editable.of([
          EditorState.readOnly.of(readOnly || disabled),
          EditorView.editable.of(!readOnly && !disabled),
        ]),
        attributes.of(
          EditorView.contentAttributes.of({
            'aria-label': 'QA markdown',
            'aria-multiline': 'true',
            'aria-invalid': String(invalid),
            ...(describedBy ? { 'aria-describedby': describedBy } : {}),
            ...(disabled ? { 'aria-disabled': 'true' } : {}),
            spellcheck: 'false',
          })
        ),
        EditorView.updateListener.of((update) => {
          if (
            update.docChanged &&
            !update.transactions.some((transaction) =>
              transaction.annotation(externalDocument)
            )
          )
            callbacks.current.onChange(update.state.doc.toString());
          if (update.selectionSet || update.viewportChanged)
            publishActive(update.view, update.selectionSet);
        }),
        EditorView.domEventHandlers({
          blur: () => {
            callbacks.current.onBlur();
            return false;
          },
          focus: (_event, view) => {
            requestAnimationFrame(() => publishActive(view, true));
            return false;
          },
        }),
      ],
    });
    viewRef.current = view;
    requestAnimationFrame(() => publishActive(view, false));
    return () => {
      viewRef.current = null;
      view.destroy();
    };
    // The editor instance is intentionally stable. Compartments and refs update it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const view = viewRef.current;
    if (!view || view.state.doc.toString() === value) return;
    view.dispatch({
      changes: { from: 0, to: view.state.doc.length, insert: value },
      annotations: externalDocument.of(true),
    });
  }, [value]);

  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    view.dispatch({
      effects: editable.reconfigure([
        EditorState.readOnly.of(readOnly || disabled),
        EditorView.editable.of(!readOnly && !disabled),
      ]),
    });
  }, [readOnly, disabled, editable]);

  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    view.dispatch({
      effects: attributes.reconfigure(
        EditorView.contentAttributes.of({
          'aria-label': 'QA markdown',
          'aria-multiline': 'true',
          'aria-invalid': String(invalid),
          ...(describedBy ? { 'aria-describedby': describedBy } : {}),
          ...(disabled ? { 'aria-disabled': 'true' } : {}),
          spellcheck: 'false',
        })
      ),
    });
  }, [invalid, describedBy, disabled, attributes]);

  useEffect(() => {
    const view = viewRef.current;
    if (!active) {
      lastActiveKey.current = null;
      return;
    }
    if (view) requestAnimationFrame(() => publishActive(view, false));
  }, [active, outlineEntries]);

  function focusLine(line: number) {
    const view = viewRef.current;
    if (!view) return;
    const boundedLine = Math.min(Math.max(1, line), view.state.doc.lines);
    const position = view.state.doc.line(boundedLine).from;
    view.dispatch({
      selection: { anchor: position },
      effects: EditorView.scrollIntoView(position, { y: 'center' }),
    });
    view.focus();
  }

  useImperativeHandle(ref, () => ({ focusLine }), []);

  useEffect(() => {
    if (navigation) focusLine(navigation.line);
  }, [navigation]);

  return (
    <div
      className={cn(
        'flex min-h-48 flex-col overflow-hidden rounded-md border border-input bg-background',
        invalid && 'border-destructive',
        disabled && 'opacity-50',
        className
      )}
    >
      <div id={id} ref={mountRef} className="min-h-0 flex-1 overflow-hidden" />
    </div>
  );
});
