# BUG-026: Team discussion duplicates side effects and misreports markers

## Severity

High — revision duplicates workspace mutations while synthesis reports a cleaner state than actually exists.

## Area

Teams → level 3 discussion/shared scratchpad execution

## Prerequisites

- Team `QA 2026-08-14 Discussion Team`
- Run `tr_0e8076f946fe46288067c1c273abea67`

## Reproduction

1. Run three stages that each append one distinct marker to a shared scratchpad.
2. Enable team dialogue so feedback causes revision.
3. Inspect Researcher session details, the file, and coordinator output.

## Actual result

The Researcher revision acknowledges that it must not append a second marker, yet reports `PRIMARY, RESEARCHER, REVIEWER, RESEARCHER`. The retained file ends with another `RESEARCHER` and `REVIEWER`; both revision stages repeated file side effects.

The coordinator nevertheless reports only `PRIMARY, RESEARCHER, REVIEWER` as the exact markers found, misrepresenting retained workspace state.

## Expected result

Discussion revisions should be side-effect-free by default or use idempotent writes. Final synthesis should verify and truthfully report current artifact content.

## Reproducibility

Confirmed from the retained run, sessions, and shared file on 2026-08-14.

## Impact

Repeated side effects can corrupt collaborative artifacts, while inaccurate synthesis hides the corruption.

## Suggested fix

- Separate initial execution from revision and disable mutating tools during revision unless required.
- Add idempotency keys/mutation guards for reruns.
- Re-read shared artifacts before synthesis.
- Test one marker per stage and exact output/file agreement.

## Evidence

- [Researcher stored session reports duplicate marker](../../evidence/team-node-session-details.png)
- [Completed team summary](../../evidence/team-discussion-completed.png)

