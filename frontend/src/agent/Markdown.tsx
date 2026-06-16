import { Fragment } from "react";
import type { ReactNode } from "react";

/**
 * Minimal, safe Markdown renderer for assistant chat bubbles (GEO-21).
 *
 * The agent replies in the small Markdown subset Gemini emits — paragraphs, ordered/unordered
 * lists, **bold**, *italic* / _italic_, and `inline code`. We build React nodes DIRECTLY (never
 * dangerouslySetInnerHTML), so no markup or script from model output can be injected. Anything we
 * don't recognise falls through as plain text. Intentionally non-nested and line-based — it matches
 * what the model produces, not the full CommonMark grammar.
 */

const ORDERED = /^\s*\d+\.\s+/;
const UNORDERED = /^\s*[-*]\s+/;
// One token at a time: `code`, **bold**, then *italic* / _italic_ (bold tried before italic).
const INLINE = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*\n]+\*|_[^_\n]+_)/g;

/** Render inline emphasis/code within a single block of text. */
function renderInline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let k = 0;
  let m: RegExpExecArray | null;
  INLINE.lastIndex = 0;
  while ((m = INLINE.exec(text)) !== null) {
    if (m.index > last) out.push(<Fragment key={k++}>{text.slice(last, m.index)}</Fragment>);
    const tok = m[0];
    if (tok.startsWith("`")) out.push(<code key={k++}>{tok.slice(1, -1)}</code>);
    else if (tok.startsWith("**")) out.push(<strong key={k++}>{tok.slice(2, -2)}</strong>);
    else out.push(<em key={k++}>{tok.slice(1, -1)}</em>);
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(<Fragment key={k++}>{text.slice(last)}</Fragment>);
  return out;
}

export function Markdown({ text }: { text: string }) {
  const lines = text.split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let b = 0;
  while (i < lines.length) {
    if (lines[i].trim() === "") {
      i++;
      continue;
    }
    if (ORDERED.test(lines[i])) {
      const items: string[] = [];
      while (i < lines.length && ORDERED.test(lines[i])) items.push(lines[i++].replace(ORDERED, ""));
      blocks.push(
        <ol key={b++}>
          {items.map((it, j) => (
            <li key={j}>{renderInline(it)}</li>
          ))}
        </ol>,
      );
      continue;
    }
    if (UNORDERED.test(lines[i])) {
      const items: string[] = [];
      while (i < lines.length && UNORDERED.test(lines[i])) items.push(lines[i++].replace(UNORDERED, ""));
      blocks.push(
        <ul key={b++}>
          {items.map((it, j) => (
            <li key={j}>{renderInline(it)}</li>
          ))}
        </ul>,
      );
      continue;
    }
    const para: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !ORDERED.test(lines[i]) &&
      !UNORDERED.test(lines[i])
    ) {
      para.push(lines[i++]);
    }
    blocks.push(<p key={b++}>{renderInline(para.join("\n"))}</p>);
  }
  return <>{blocks}</>;
}
