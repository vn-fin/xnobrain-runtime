import { useEffect, useRef } from 'react';

/** Closes a custom popup when focus moves outside it or Escape is pressed. */
export function useDismissibleLayer<T extends HTMLElement>(open: boolean, onDismiss: () => void) {
  const root = useRef<T>(null);
  const dismiss = useRef(onDismiss);
  dismiss.current = onDismiss;

  useEffect(() => {
    if (!open) return undefined;
    const closeOutside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) dismiss.current();
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') dismiss.current();
    };
    document.addEventListener('pointerdown', closeOutside);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('pointerdown', closeOutside);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [open]);

  return root;
}
