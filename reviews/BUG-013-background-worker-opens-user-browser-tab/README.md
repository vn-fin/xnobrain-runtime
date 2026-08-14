# BUG-013: Background research worker opens a visible tab in the user browser

## Severity

High — background work can interrupt the user’s browser, expose browsing activity, and make UI automation or manual navigation target the wrong tab.

## Area

Chat delegation → worker browser/research tools

## Reproduction

1. In retained session `20260814_124514_28927b`, delegate five real conference research tasks.
2. Keep the XNOBrain application open in the headed Chrome browser.
3. Wait for a worker to begin source discovery.
4. Inspect the browser context’s pages/tabs.

## Actual result

The browser context contains both:

- a new visible Google Search tab opened by a research worker; and
- the original XNOBrain session tab.

The new search tab becomes the first/focused page in the context. A user can be pulled away from the application, and scripts or accessibility tooling that target the active/first tab begin reading the worker’s Google results instead of XNOBrain.

The delegation itself continues correctly in the original app tab.

## Expected result

Background agents should use an isolated browser context/profile that cannot create tabs, change navigation, consume cookies, or steal focus in the user’s interactive browser. The UI should receive sanitized progress/events only.

## Suggested fix

- Give each run/worker an isolated headless browser context or sandbox browser service.
- Do not attach worker browsing to the application’s remote-debugging profile.
- Scope cookies, downloads, storage, permissions, and page lifecycle to the worker run.
- Relay browser activity summaries and optional screenshots through the task event stream instead of exposing the worker tab.
- Add an integration test that runs browser-backed delegation while asserting the user context’s page count, active URL, and focus remain unchanged.

## Evidence

- [Worker-created Google tab](../../evidence/research-pdf-near-timeout.png)
- [Delegation still running in original app tab](../../evidence/research-pdf-timeout-state.png)
