import { memo, type ReactNode } from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import rehypeHighlight from 'rehype-highlight';
import { FileText, Image as ImageIcon } from 'lucide-react';
import 'katex/dist/katex.min.css';
import 'highlight.js/styles/github-dark.css';

const IMAGE_REF = /\.(png|jpe?g|gif|webp|svg|bmp|ico|avif)$/i;
const FILE_REF = /\.(png|jpe?g|gif|webp|svg|bmp|ico|avif|pdf|md|mdx|txt|log|csv|tsv|json|ya?ml|toml|xml|html?|css|scss|py|ipynb|jsx?|tsx?|sh|sql|doc|docx|xls|xlsx|ppt|pptx|zip|tar|gz)$/i;

/** A code span is a previewable file reference when it's a whitespace-free path ending in a known extension. */
function isFileRef(text: string): boolean {
  const value = text.trim();
  return value.length > 0 && !/\s/.test(value) && FILE_REF.test(value);
}

const baseName = (path: string) => path.split('/').filter(Boolean).pop() ?? path;

/** Reads the `language-xxx` class off a fenced code block's <code> child. */
function languageOf(children: ReactNode): string | undefined {
  const child = Array.isArray(children) ? children[0] : children;
  if (child && typeof child === 'object' && 'props' in child) {
    const className = String((child as { props?: { className?: string } }).props?.className ?? '');
    return /language-(\w[\w+-]*)/.exec(className)?.[1];
  }
  return undefined;
}

function buildComponents(onOpenFile?: (path: string) => void): Components {
  return {
    code({ className, children, ...props }) {
      const text = String(children ?? '');
      const isBlock = /language-/.test(className ?? '') || text.includes('\n');
      if (!isBlock) {
        const inner = text;
        if (onOpenFile && isFileRef(inner)) {
          return (
            <button
              type="button"
              className="message-file-ref"
              title={`Quick view · ${inner}`}
              onClick={() => onOpenFile(inner)}
            >
              {IMAGE_REF.test(inner) ? <ImageIcon size={13} /> : <FileText size={13} />}
              <span>{baseName(inner)}</span>
            </button>
          );
        }
        return <code className="message-inline-code">{children}</code>;
      }
      // Block code: keep rehype-highlight's classes/spans intact.
      return <code className={className} {...props}>{children}</code>;
    },
    pre({ children }) {
      return (
        <pre className="message-code-block" data-language={languageOf(children)}>
          {children}
        </pre>
      );
    },
    a({ href, children }) {
      return (
        <a href={href} target="_blank" rel="noopener noreferrer">
          {children}
        </a>
      );
    },
    table({ children }) {
      return (
        <div className="message-table-wrap">
          <table>{children}</table>
        </div>
      );
    },
  };
}

function MarkdownBase({ content, onOpenFile }: { content: string; onOpenFile?: (path: string) => void }) {
  return (
    <div className="markdown-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex, [rehypeHighlight, { detect: true, ignoreMissing: true }]]}
        components={buildComponents(onOpenFile)}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

export const Markdown = memo(MarkdownBase);
