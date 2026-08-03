import { useLayoutEffect, useMemo, useRef, useState } from 'react';
import hljs from 'highlight.js/lib/common';
import dart from 'highlight.js/lib/languages/dart';
import dockerfile from 'highlight.js/lib/languages/dockerfile';
import 'highlight.js/styles/github-dark.css';
import { highlightLanguageForPath } from '../api/mappers/workspace';

hljs.registerLanguage('dart', dart);
hljs.registerLanguage('dockerfile', dockerfile);

const MAX_HIGHLIGHT_CHARS = 1_000_000;
const EDITOR_LINE_HEIGHT = 13 * 1.55;
const EDITOR_VERTICAL_PADDING = 28;

function escaped(content: string): string {
  return content
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;');
}

function shebangLanguage(content: string): string | undefined {
  const firstLine = content.split('\n', 1)[0].toLowerCase();
  if (!firstLine.startsWith('#!')) return undefined;
  if (/\b(python|python3)\b/.test(firstLine)) return 'python';
  if (/\b(node|deno|bun)\b/.test(firstLine)) return 'javascript';
  if (/\b(bash|sh|zsh|fish)\b/.test(firstLine)) return 'bash';
  if (/\bruby\b/.test(firstLine)) return 'ruby';
  if (/\bperl\b/.test(firstLine)) return 'perl';
  return undefined;
}

export function highlightedSource(
  content: string,
  path: string,
): { html: string; language: string; highlighted: boolean } {
  const requested = highlightLanguageForPath(path);
  const inferred = requested === 'plaintext' ? shebangLanguage(content) : undefined;
  const language = hljs.getLanguage(inferred ?? requested) ? (inferred ?? requested) : 'plaintext';
  if (content.length > MAX_HIGHLIGHT_CHARS) {
    return { html: escaped(content), language, highlighted: false };
  }
  return {
    html: hljs.highlight(content, { language, ignoreIllegals: true }).value,
    language,
    highlighted: language !== 'plaintext',
  };
}

export function lineNumberText(content: string, minimumLines = 1): string {
  const count = Math.max(1, minimumLines, content.split('\n').length);
  return Array.from({ length: count }, (_, index) => String(index + 1)).join('\n');
}

function useVisibleLineCount(ref: React.RefObject<HTMLElement | null>, fillViewport: boolean) {
  const [minimumLines, setMinimumLines] = useState(1);

  useLayoutEffect(() => {
    if (!fillViewport) {
      setMinimumLines(1);
      return undefined;
    }
    const element = ref.current;
    if (!element) return undefined;
    const update = () => {
      const usableHeight = Math.max(0, element.clientHeight - EDITOR_VERTICAL_PADDING);
      setMinimumLines(Math.max(1, Math.ceil(usableHeight / EDITOR_LINE_HEIGHT)));
    };
    update();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(update);
    observer?.observe(element);
    window.addEventListener('resize', update);
    return () => {
      observer?.disconnect();
      window.removeEventListener('resize', update);
    };
  }, [fillViewport, ref]);

  return minimumLines;
}

export function NumberedTextEditor({
  value,
  ariaLabel,
  disabled,
  autoFocus,
  onChange,
}: {
  value: string;
  ariaLabel: string;
  disabled?: boolean;
  autoFocus?: boolean;
  onChange: (value: string) => void;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const gutterRef = useRef<HTMLPreElement>(null);
  const minimumLines = useVisibleLineCount(rootRef, true);
  const lineNumbers = useMemo(() => lineNumberText(value, minimumLines), [minimumLines, value]);

  return (
    <div className="gd-numbered-editor" ref={rootRef}>
      <pre ref={gutterRef} className="gd-code-lines" aria-hidden="true">{lineNumbers}</pre>
      <textarea
        aria-label={ariaLabel}
        autoFocus={autoFocus}
        disabled={disabled}
        value={value}
        wrap="off"
        onChange={(event) => onChange(event.target.value)}
        onScroll={(event) => {
          if (gutterRef.current) gutterRef.current.scrollTop = event.currentTarget.scrollTop;
        }}
      />
    </div>
  );
}

export default function CodeViewer({ content, path, fillViewport = false }: { content: string; path: string; fillViewport?: boolean }) {
  const rootRef = useRef<HTMLDivElement>(null);
  const highlighted = useMemo(() => highlightedSource(content, path), [content, path]);
  const minimumLines = useVisibleLineCount(rootRef, fillViewport);
  const lineNumbers = useMemo(() => lineNumberText(content, minimumLines), [content, minimumLines]);

  return (
    <div
      className="gd-code-editor"
      ref={rootRef}
      data-language={highlighted.language}
      data-highlighted={highlighted.highlighted}
    >
      <pre className="gd-code-lines" aria-hidden="true">{lineNumbers}</pre>
      <pre className="gd-code">
        <code
          className={`hljs language-${highlighted.language}`}
          dangerouslySetInnerHTML={{ __html: highlighted.html || '&nbsp;' }}
        />
      </pre>
    </div>
  );
}
