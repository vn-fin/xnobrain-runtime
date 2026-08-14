# BUG-016: Generated PDF renders source URLs as plain text instead of clickable links

## Severity

Medium — the report looks complete, but readers cannot follow its source citations from the PDF.

## Area

Agent PDF generation and Workspace PDF preview

## Prerequisites

- Retained session: `20260814_124514_28927b`
- Retained output: `qa-2026-08-14/conference-research-report.pdf`

## Reproduction

1. Ask the agent to synthesize cited KDD, NeurIPS, ICML, ICLR, and ACL research and create a PDF with links.
2. Wait for the agent’s own verification to pass.
3. Open the PDF in the Workspace preview.
4. Inspect the document annotations (or try to activate a source URL).

## Actual result

The two-page PDF contains the visible URL strings but no link annotations. `pdftohtml -xml` reports the URLs only as `<text>` nodes with no `href`, and `mutool show <pdf> 1 annots` returns no annotations. Long NeurIPS and ICLR URLs are also wrapped in the middle of path tokens.

The agent reports `PASS: 5 results, report coverage, and PDF text verified (2 pages)`, so the canonical verification checks text presence but not interactive links.

## Expected result

Markdown links and source URLs should become valid PDF URI annotations. Link labels or URLs should wrap without corrupting readability or the destination, and verification should assert the expected URI targets.

## Reproducibility

Reproduced with the installed PDF tooling in the local development environment on 2026-08-14.

## Impact

Citation-heavy reports require manual copying of long, line-broken URLs and can appear verified even though a requested deliverable property is missing.

## Suggested fix

- Render Markdown anchors through ReportLab link markup or explicit `canvas.linkURL` annotations.
- Preserve the complete URI as the annotation target independently of visual wrapping.
- Extend PDF verification to enumerate annotations and compare their URI targets with the report sources.

## Evidence

- [Workspace PDF preview](../../evidence/workspace-pdf-preview.png)
- [Completed research run](../../evidence/research-pdf-completed.png)

