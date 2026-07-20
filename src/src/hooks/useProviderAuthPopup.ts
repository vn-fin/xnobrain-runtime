import { useCallback, useEffect, useRef } from 'react';
import {
  navigateProviderAuthPopup,
  readProviderOAuthCallbackFromClipboard,
} from '../utils/providerAuth';

export function useProviderAuthPopup({
  active,
  onCallbackText,
  onClipboardText,
}: {
  active: boolean;
  onCallbackText: (callbackText: string) => boolean | void | Promise<boolean | void>;
  onClipboardText: (callbackText: string) => void;
}) {
  const callbackRef = useRef(onCallbackText);
  const clipboardRef = useRef(onClipboardText);
  const handledRef = useRef('');
  const submittingRef = useRef(false);

  useEffect(() => {
    callbackRef.current = onCallbackText;
    clipboardRef.current = onClipboardText;
  }, [onCallbackText, onClipboardText]);

  // Reset the last-handled guard whenever the flow (re)starts, so the same URL
  // can be pasted again on a new attempt.
  useEffect(() => {
    if (!active) handledRef.current = '';
  }, [active]);

  const handleCallback = useCallback(async (callbackText: string) => {
    if (!callbackText || submittingRef.current || handledRef.current === callbackText) return false;
    submittingRef.current = true;
    clipboardRef.current(callbackText);
    try {
      const ok = (await callbackRef.current(callbackText)) !== false;
      // Only remember the text once it actually submitted, so a failed attempt
      // (e.g. a transient error) can be retried by pasting the same URL again.
      if (ok) handledRef.current = callbackText;
      return ok;
    } finally {
      submittingRef.current = false;
    }
  }, []);

  // Manual "paste from clipboard" button. Reading is only triggered by an
  // explicit user click — there is no automatic clipboard capture or submit.
  const pasteFromClipboard = useCallback(async () => {
    const callbackText = await readProviderOAuthCallbackFromClipboard();
    if (!callbackText) return false;
    return handleCallback(callbackText);
  }, [handleCallback]);

  return {
    openPopup: navigateProviderAuthPopup,
    pasteFromClipboard,
  };
}
