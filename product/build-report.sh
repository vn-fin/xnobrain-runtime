#!/usr/bin/env bash
# Merge the Brain4All report chapters (product/reports/*.md) into one PDF.
# Requires: pandoc + weasyprint (both present on this machine).
#
#   ./product/build-report.sh
#
# Output: product/Brain4All-Report.pdf
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LANG_ARG="${1:-en}"
case "$LANG_ARG" in
  en) REPORTS="$HERE/reports";    OUT="$HERE/Brain4All-Report.pdf"
      TITLE="Brain4All — Twin Terminal"; SUBTITLE="Product Report & Development Plan"; DATED="July 2026" ;;
  vi) REPORTS="$HERE/reports-vi"; OUT="$HERE/Brain4All-Report-VI.pdf"
      TITLE="Brain4All — Twin Terminal"; SUBTITLE="Báo cáo Sản phẩm & Kế hoạch Phát triển"; DATED="Tháng 7, 2026" ;;
  *)  echo "usage: $0 [en|vi]" >&2; exit 1 ;;
esac
BUILD="$HERE/.report-build-$LANG_ARG"
mkdir -p "$BUILD"

# Narrative order (not numeric order): summary -> why -> what exists -> moat ->
# business -> how we build -> roadmap -> appendix.
#   00 Executive Summary        02 Product Vision           04 Market & Numbers
#   05 Startup Landscape        01 Current State            03 Core Tech & Competitors
#   06 Certification Moat       07 Business Model           11 Integration Architecture
#   10 Build vs Rewrite         08 Roadmap                  09 Appendix (use cases)
ORDER=(
  00-index
  02-vision-twin-terminal
  04-market-and-numbers
  05-startup-landscape
  01-current-state
  03-core-tech-and-competitors
  06-certification-moat
  07-business-model-and-licensing
  11-integration-architecture
  10-golang-rewrite-analysis
  08-roadmap
  09-hermes-use-cases
)

COMBINED="$BUILD/combined.md"
: > "$COMBINED"

for name in "${ORDER[@]}"; do
  f="$REPORTS/$name.md"
  [ -f "$f" ] || { echo "!! missing $f" >&2; exit 1; }
  # Per-file cleanup for a single-document PDF:
  #  1) strip internal cross-file links   [text](NN-name.md#x) -> text (keep http links)
  #  2) strip intra-document anchor links  [text](#anchor)     -> text
  #  3) strip the "NN · " chapter-number prefix from the top H1
  #  4) turn any residual bare "NN-slug.md" reference into a readable section name
  #  5) map a few emoji to font-safe glyphs weasyprint renders cleanly
  sed -E \
    -e 's/\[([^]]+)\]\([0-9]{2}-[a-z0-9-]+\.md[^)]*\)/\1/g' \
    -e 's/\[([^]]+)\]\(#[^)]*\)/\1/g' \
    -e 's/^(#{1,2}) [0-9]{1,2} · /\1 /' \
    "$f" \
  | sed \
    -e 's/00-index\.md/the Executive Summary/g' \
    -e 's/01-current-state\.md/the Current State section/g' \
    -e 's/02-vision-twin-terminal\.md/the Vision section/g' \
    -e 's/03-core-tech-and-competitors\.md/the Core Technology section/g' \
    -e 's/04-market-and-numbers\.md/the Market section/g' \
    -e 's/05-startup-landscape\.md/the Startup Landscape section/g' \
    -e 's/06-certification-moat\.md/the Certification Moat section/g' \
    -e 's/07-business-model-and-licensing\.md/the Business Model section/g' \
    -e 's/08-roadmap\.md/the Roadmap section/g' \
    -e 's/09-hermes-use-cases\.md/the Use-Case Appendix/g' \
    -e 's/10-golang-rewrite-analysis\.md/the Build-vs-Rewrite section/g' \
    -e 's/11-integration-architecture\.md/the Integration Architecture section/g' \
    -e 's/✅/✔/g' -e 's/❌/✘/g' -e 's/🟡/◐/g' -e 's/⚠️/⚠/g' -e 's/📌/•/g' \
    -e 's/📊/■/g' -e 's/🟢/●/g' \
    >> "$COMBINED"
  printf '\n\n' >> "$COMBINED"
done

# Inline stylesheet (weasyprint reads it from <head>; no external file to resolve).
cat > "$BUILD/head.html" <<'CSS'
<style>
  @page {
    size: A4; margin: 2cm 1.8cm;
    @bottom-center { content: counter(page); font-size: 9pt; color: #8a8a8a; }
    @top-right { content: "Brain4All — Twin Terminal"; font-size: 8pt; color: #b3b3b3; }
  }
  @page:first { @top-right { content: ""; } }
  body { font-family: "DejaVu Sans","Liberation Sans",Arial,sans-serif;
         font-size: 10.3pt; line-height: 1.5; color: #1c1c1c; }
  h1:not(.title) { page-break-before: always; font-size: 19pt; color: #17211F;
       border-bottom: 2px solid #2B4A44; padding-bottom: 5px; margin-top: 0; }
  h2 { font-size: 13.5pt; color: #2B4A44; margin-top: 1.3em;
       border-bottom: 1px solid #dfe3db; padding-bottom: 2px; }
  h3 { font-size: 11.3pt; color: #3a3a3a; margin-top: 1.1em; }
  h4 { font-size: 10.3pt; color: #4a5551; }
  p, li { orphans: 2; widows: 2; }
  table { border-collapse: collapse; width: 100%; font-size: 8.6pt; margin: 10px 0;
          page-break-inside: avoid; }
  th, td { border: 1px solid #c6ccc1; padding: 4px 7px; text-align: left; vertical-align: top; }
  th { background: #eef1ec; color: #17211F; }
  tr:nth-child(even) td { background: #fafbf8; }
  code { font-family: "DejaVu Sans Mono",monospace; font-size: 8.8pt;
         background: #f4f6f1; padding: 0 2px; border-radius: 2px; }
  pre { background: #f4f6f1; border: 1px solid #dfe3db; border-radius: 4px;
        padding: 8px 10px; overflow-x: auto; font-size: 8.2pt; line-height: 1.35;
        white-space: pre-wrap; page-break-inside: avoid; }
  pre code { background: none; padding: 0; }
  blockquote { border-left: 3px solid #D9C88F; background: #faf8f0; color: #4a5551;
               margin: 0.8em 0; padding: 3px 12px; font-size: 9.6pt; }
  a { color: #2B4A44; text-decoration: none; }
  hr { border: none; border-top: 1px solid #dfe3db; margin: 1.4em 0; }
  /* Title block + table of contents */
  header#title-block-header { text-align: center; margin: 22% 0 0; page-break-after: always; }
  header#title-block-header .title { font-size: 30pt; color: #17211F; border: none; }
  header#title-block-header .subtitle { font-size: 15pt; color: #2B4A44; margin-top: 6px; }
  header#title-block-header .date { font-size: 11pt; color: #7c857f; margin-top: 18px; }
  nav#TOC { page-break-after: always; }
  nav#TOC > ul { list-style: none; padding-left: 0; }
  nav#TOC ul ul { list-style: none; padding-left: 1.2em; font-size: 9.4pt; color: #4a5551; }
  nav#TOC a { color: #17211F; }
</style>
CSS

echo ">> merging ${#ORDER[@]} chapters -> $COMBINED"
echo ">> rendering PDF via pandoc + weasyprint ..."
pandoc "$COMBINED" \
  -f gfm-tex_math_dollars \
  -t html5 \
  --standalone \
  --pdf-engine=weasyprint \
  --toc --toc-depth=2 \
  --metadata title="$TITLE" \
  --metadata subtitle="$SUBTITLE" \
  --metadata date="$DATED" \
  --include-in-header="$BUILD/head.html" \
  -o "$OUT"

echo ">> done: $OUT"
ls -lh "$OUT" | awk '{print "   size:", $5}'
