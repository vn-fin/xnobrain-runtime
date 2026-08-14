export const NAVIGATION_REQUEST_EVENT = 'xnobrain:navigation-request';

export type NavigationRequestDetail = { proceed: () => void };

/** Give dirty editors one synchronous chance to defer an SPA navigation. */
export function requestNavigation(proceed: () => void): boolean {
  const event = new CustomEvent<NavigationRequestDetail>(NAVIGATION_REQUEST_EVENT, {
    cancelable: true,
    detail: { proceed },
  });
  if (!window.dispatchEvent(event)) return false;
  proceed();
  return true;
}
