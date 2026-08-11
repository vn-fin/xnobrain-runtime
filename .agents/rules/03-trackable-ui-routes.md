# Trackable UI Routes

- Give every primary tab and meaningful selected entity or subview a stable URL. This includes detail drawers, task/job/session views, and route-backed popups that users may need to share, reload, or revisit.
- Update the URL when the user opens or selects the view; restore the same UI state from a pasted URL, reload, and browser Back/Forward navigation.
- Return to the parent route when the selected detail closes. Encode path segments and preserve required scope identifiers in query parameters when an entity ID is not globally unique.
- Keep ephemeral state such as hover cards, menus, tooltips, confirmation prompts, and unsaved form input out of the URL.
- Add route parsing/serialization tests and a component test that verifies selection delegates to route navigation.
