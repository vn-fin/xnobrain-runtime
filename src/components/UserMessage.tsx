import { useEffect, useState } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
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
  const collapsible = content.length > COLLAPSED_MESSAGE_LENGTH
    || content.split('\n').length > COLLAPSED_MESSAGE_LINES;

  useEffect(() => setExpanded(false), [content]);

  return (
    <div className={`user-bubble${collapsible ? ' collapsible' : ''}${collapsible && !expanded ? ' collapsed' : ''}`}>
      <div className="user-bubble-content">
        <Markdown content={content} onOpenFile={onOpenFile} />
      </div>
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
    </div>
  );
}
