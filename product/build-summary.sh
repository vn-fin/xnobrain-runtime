#!/usr/bin/env bash
# Render the standalone one-page executive brief to PDF, EN and VI.
#   ./product/build-summary.sh
# Output: product/XNOBrain-Summary-EN.pdf , product/XNOBrain-Summary-VI.pdf
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD="$HERE/.summary-build"
mkdir -p "$BUILD"

cat > "$BUILD/head.html" <<'CSS'
<style>
  @page { size: A4; margin: 1.9cm 1.9cm;
    @bottom-center { content: counter(page); font-size: 9pt; color: #8a8a8a; } }
  body { font-family: "DejaVu Sans","Liberation Sans",Arial,sans-serif;
         font-size: 10.5pt; line-height: 1.5; color: #1c1c1c; }
  h1 { font-size: 21pt; color: #17211F; border-bottom: 2px solid #2B4A44;
       padding-bottom: 6px; margin: 0 0 0.5em; }
  h2 { font-size: 13pt; color: #2B4A44; margin-top: 1.15em;
       border-bottom: 1px solid #e2e6de; padding-bottom: 2px; }
  ul { margin: 0.3em 0 0.6em; padding-left: 1.2em; }
  li { margin: 0.15em 0; }
  strong { color: #17211F; }
  blockquote { border-left: 3px solid #D9C88F; background: #faf8f0; color: #4a5551;
               margin: 0.6em 0; padding: 3px 12px; }
  a { color: #2B4A44; text-decoration: none; }
  hr { border: none; border-top: 1px solid #e2e6de; margin: 1.2em 0; }
</style>
CSS

build () {  # $1=lang  $2=OUTNAME  $3=pagetitle
  local lang="$1" out="$HERE/$2" pt="$3"
  echo ">> rendering $out"
  pandoc "$HERE/summary/summary-$lang.md" \
    -f gfm-tex_math_dollars -t html5 --standalone \
    --pdf-engine=weasyprint \
    --metadata pagetitle="$pt" \
    --include-in-header="$BUILD/head.html" \
    -o "$out"
  ls -lh "$out" | awk '{print "   size:", $5}'
}

build en "XNOBrain-Summary-EN.pdf" "XNOBrain — Twin Terminal · Executive Brief"
build vi "XNOBrain-Summary-VI.pdf" "XNOBrain — Twin Terminal · Tóm tắt điều hành"
echo ">> done."
