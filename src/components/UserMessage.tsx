import { useEffect, useState } from 'react';
import { Check, ChevronDown, ChevronUp, Copy } from 'lucide-react';
import { Markdown } from './Markdown';

const COLLAPSED_MESSAGE_LENGTH = 240;
const COLLAPSED_MESSAGE_LINES = 4;

export function UserMessage({
  content,
  onOpenFile,
}: {
  content: string;
  onOpenFile?: (path: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const collapsible = content.length > COLLAPSED_MESSAGE_LENGTH
    || content.split('\n').length > COLLAPSED_MESSAGE_LINES;

  useEffect(() => {
    setExpanded(false);
    setCopied(false);
  }, [content]);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 2_000);
    return () => window.clearTimeout(timer);
  }, [copied]);

  const copyMessage = async () => {
    if (!navigator.clipboard?.writeText) return;
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div className={`user-bubble${collapsible ? ' collapsible' : ''}${collapsible && !expanded ? ' collapsed' : ''}`}>
      <div className="user-bubble-content">
        <Markdown content={content} onOpenFile={onOpenFile} />
      </div>
      <div className="user-bubble-actions">
        {collapsible && (
          <button
            type="button"
            className="user-bubble-toggle"
            aria-expanded={expanded}
            aria-label={expanded ? 'Collapse message' : 'Expand message'}
            title={expanded ? 'Collapse text' : 'Expand text'}
            onClick={() => setExpanded((current) => !current)}
          >
            {expanded ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
          </button>
        )}
        <button
          type="button"
          className={`user-bubble-copy${copied ? ' copied' : ''}`}
          aria-label={copied ? 'User message copied' : 'Copy user message'}
          title={copied ? 'Copied' : 'Copy message'}
          onClick={() => void copyMessage()}
        >
          {copied ? <Check size={15} /> : <Copy size={15} />}
        </button>
      </div>
    </div>
  );
}
