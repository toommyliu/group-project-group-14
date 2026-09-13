/** Convert the parser's one-based line number to a UTF-16 textarea offset. */
export function sourceLineOffset(value: string, line: number): number | null {
  if (!Number.isInteger(line) || line < 1) return null;
  if (line === 1) return 0;
  let current = 1;
  // oxlint-disable-next-line no-control-regex -- Match Python splitlines, including its control-character boundaries.
  const lineBreaks = /\r\n|[\n\r\v\f\u001c-\u001e\u0085\u2028\u2029]/g;
  for (const match of value.matchAll(lineBreaks)) {
    current += 1;
    if (current === line) return match.index + match[0].length;
  }
  return null;
}

/** Measure wrapped text only on navigation, without changing the editor's value. */
export function jumpToSourceLine(editor: HTMLTextAreaElement, line: number) {
  const offset = sourceLineOffset(editor.value, line);
  if (offset === null) return;
  const style = getComputedStyle(editor);
  const mirror = document.createElement('div');
  Object.assign(mirror.style, {
    position: 'fixed',
    top: '0',
    left: '0',
    visibility: 'hidden',
    pointerEvents: 'none',
    boxSizing: 'border-box',
    width: `${editor.clientWidth}px`,
    padding: style.padding,
    fontFamily: style.fontFamily,
    fontSize: style.fontSize,
    fontWeight: style.fontWeight,
    fontStyle: style.fontStyle,
    lineHeight: style.lineHeight,
    letterSpacing: style.letterSpacing,
    wordSpacing: style.wordSpacing,
    tabSize: style.tabSize,
    textIndent: style.textIndent,
    textTransform: style.textTransform,
    direction: style.direction,
    whiteSpace: 'pre-wrap',
    overflowWrap: 'break-word',
    wordBreak: style.wordBreak,
  });
  mirror.textContent = editor.value.slice(0, offset);
  const marker = document.createElement('span');
  marker.textContent = '\u200b';
  mirror.append(marker);
  document.body.append(mirror);
  try {
    const top =
      marker.getBoundingClientRect().top - mirror.getBoundingClientRect().top;
    editor.focus({ preventScroll: true });
    editor.setSelectionRange(offset, offset);
    editor.scrollTop = Math.max(0, top - editor.clientHeight / 3);
    editor.scrollIntoView({ block: 'nearest', behavior: 'instant' });
  } finally {
    mirror.remove();
  }
}
