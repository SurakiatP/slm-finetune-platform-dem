import { Fragment, type ReactNode } from "react";

/**
 * Minimal, safe Markdown renderer for model output. It builds React elements
 * only (never innerHTML), so model text cannot inject markup. Supports
 * paragraphs, headings, bullet/numbered lists, fenced code blocks, inline
 * code, **bold**, *italic* and __bold__.
 */
export function Markdown({ text }: { text: string }) {
  return <>{renderBlocks(text)}</>;
}

function renderBlocks(text: string): ReactNode[] {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const out: ReactNode[] = [];
  let i = 0;
  let key = 0;

  while (i < lines.length) {
    const line = lines[i];

    // Fenced code block (an unterminated fence while streaming runs to the end).
    if (/^\s*```/.test(line)) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) body.push(lines[i++]);
      i++;
      out.push(
        <pre key={key++} className="my-1.5 overflow-x-auto rounded-md bg-muted px-3 py-2 font-mono text-xs">
          <code>{body.join("\n")}</code>
        </pre>,
      );
      continue;
    }

    if (!line.trim()) {
      i++;
      continue;
    }

    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      out.push(
        <p key={key++} className="mt-2 font-semibold first:mt-0">
          {renderInline(heading[2])}
        </p>,
      );
      i++;
      continue;
    }

    const bullet = /^\s*[-*+]\s+/;
    const numbered = /^\s*\d+[.)]\s+/;
    if (bullet.test(line) || numbered.test(line)) {
      const ordered = numbered.test(line);
      const pattern = ordered ? numbered : bullet;
      const items: string[] = [];
      while (i < lines.length && pattern.test(lines[i])) items.push(lines[i++].replace(pattern, ""));
      const ListTag = ordered ? "ol" : "ul";
      out.push(
        <ListTag key={key++} className={`my-1 space-y-0.5 pl-5 ${ordered ? "list-decimal" : "list-disc"}`}>
          {items.map((item, n) => (
            <li key={n}>{renderInline(item)}</li>
          ))}
        </ListTag>,
      );
      continue;
    }

    // Paragraph: consecutive non-special lines, keeping single line breaks.
    const para: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^\s*```/.test(lines[i]) &&
      !/^#{1,6}\s+/.test(lines[i]) &&
      !bullet.test(lines[i]) &&
      !numbered.test(lines[i])
    ) {
      para.push(lines[i++]);
    }
    out.push(
      <p key={key++} className="my-1 first:mt-0 last:mb-0">
        {para.map((p, n) => (
          <Fragment key={n}>
            {n > 0 && <br />}
            {renderInline(p)}
          </Fragment>
        ))}
      </p>,
    );
  }
  return out;
}

const INLINE = /(`[^`]+`|\*\*[^*]+\*\*|__[^_]+__|\*[^*\s][^*]*\*)/g;

function renderInline(text: string): ReactNode[] {
  return text.split(INLINE).map((part, n) => {
    if (!part) return null;
    if (part.startsWith("`") && part.endsWith("`") && part.length > 1) {
      return (
        <code key={n} className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]">
          {part.slice(1, -1)}
        </code>
      );
    }
    if ((part.startsWith("**") && part.endsWith("**")) || (part.startsWith("__") && part.endsWith("__"))) {
      if (part.length > 4) return <strong key={n}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith("*") && part.endsWith("*") && part.length > 2) {
      return <em key={n}>{part.slice(1, -1)}</em>;
    }
    return <Fragment key={n}>{part}</Fragment>;
  });
}
