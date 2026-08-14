# BUG-008: Agent settings are not reachable through desktop navigation

## Severity

High — users cannot normally edit an agent’s provider, model, reasoning effort, or approval mode after creation.

## Area

Agent chat → right inspector / agent actions

## Reproduction

1. Create or open retained agent `QA 2026-08-14 Approval Agent` (`uaphzn`).
2. Inspect the desktop header, sidebar agent menu, Manage menu, and right-inspector tabs.
3. Attempt to open **Agent settings**.
4. Directly load `/agents/uaphzn?panel=runtime` as a recovery attempt.

## Actual result

- The header exposes only **Test agent**.
- The sidebar agent menu exposes Pin, Rename, Export, and Delete.
- The right inspector exposes only Workspace, Skills, and Cron tabs.
- The Runtime section contains the **Agent settings** action, but there is no UI action that selects Runtime.
- A `?panel=runtime` deep link parses the state but leaves the right panel collapsed; opening a visible tab replaces Runtime with that tab.

The settings modal can only be reached by artificially keeping the inspector open while dispatching an in-page route-state change.

## Expected result

Agent settings should be reachable by a visible, keyboard-accessible desktop action, and a Runtime deep link should open the relevant inspector section.

## Suggested fix

- Add Runtime to the right-inspector tab list, or add **Agent settings** to the desktop header/sidebar menu.
- Initialize `rightPanelOpen` from a non-default `panel` query parameter so deep links restore visibly.
- Remove unreachable recursive Runtime/Memory/Test buttons or wire them to real destinations.
- Add desktop and narrow-viewport navigation tests from agent chat to settings and back.

## Evidence

![Agent page without a settings action](../../evidence/agent-settings-inaccessible.png)
