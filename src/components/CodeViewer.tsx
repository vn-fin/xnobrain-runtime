import { useMemo } from 'react';
import hljs from 'highlight.js/lib/common';
import dart from 'highlight.js/lib/languages/dart';
import dockerfile from 'highlight.js/lib/languages/dockerfile';
import 'highlight.js/styles/github-dark.css';
import { highlightLanguageForPath } from '../api/mappers/workspace';

hljs.registerLanguage('dart', dart);
hljs.registerLanguage('dockerfile', dockerfile);

const MAX_HIGHLIGHT_CHARS = 1_000_000;

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

export function lineNumberText(content: string): string {
  const count = Math.max(1, content.split('\n').length);
  return Array.from({ length: count }, (_, index) => String(index + 1)).join('\n');
}

export default function CodeViewer({ content, path }: { content: string; path: string }) {
  const highlighted = useMemo(() => highlightedSource(content, path), [content, path]);
  const lineNumbers = useMemo(() => lineNumberText(content), [content]);

  return (
    <div
      className="gd-code-editor"
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
