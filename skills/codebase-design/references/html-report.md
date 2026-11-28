# HTML report

An optional rendering of the architecture review's candidates as one self-contained HTML file.

## Rules

- Create a temp directory first (`dir=$(mktemp -d) && [ -n "$dir" ] || exit 1`), then write
  `"$dir/architecture-review.html"`, never into the repository. Give the user the absolute path;
  open it with `open` or `xdg-open` if the user wants.
- Local assets only: inline `<style>` and inline `<svg>`. No scripts, CDNs, web fonts or remote
  images. Put `<meta http-equiv="Content-Security-Policy" content="default-src 'none';
  style-src 'unsafe-inline'">` first in `<head>`, before any other markup, and HTML-escape every
  repo name, path and code snippet you interpolate. Confirm that tag is first in `<head>`, set
  `report` to its path, and run this quick check; it must print nothing:

  ```sh
  grep -inE '<script|<iframe|<object|<embed|<link|@import|(src|srcset)[[:space:]]*=|href[[:space:]]*=[[:space:]]*["'\'']?[[:space:]]*($|[^#"'\''[:space:]])|url\([[:space:]]*["'\'']?[[:space:]]*($|[^#"'\''[:space:]])' "$report"
  ```

## Layout

- **Header**: repository name, date and a legend: solid box is a module, dashed line is a seam, red
  arrow is a leak, thick dark box is a deep module. No introduction.
- **One `<article>` per candidate**: a title naming the deepening ("Collapse the Order intake
  pipeline"); badges for strength (`Strong` green, `Worth exploring` amber, `Speculative` gray)
  and dependency category; files in monospace; the before and after diagrams side by side, about
  320px tall; the problem and the solution in one sentence each; wins as bullets of six words or
  fewer in vocabulary terms ("Tests hit one interface"); an ADR conflict as one amber line.
- **Top recommendation**: the candidate's name, one sentence on why, and a link to its card.

If a diagram needs a paragraph to be understood, redraw the diagram.

## Diagram patterns

Pick the one that fits each candidate, and vary them.

- **Boxes and arrows**: modules as bordered `<div>`s, arrows as inline SVG lines; leaks in red.
- **Cross-section**: stacked horizontal bands for the layers a call passes through. Before: many
  thin bands doing nothing. After: one thick band named for the consolidated responsibility.
- **Mass diagram**: two rectangles per module, interface and implementation. Shallow: the interface
  rectangle is nearly as tall as the implementation. Deep: short interface, tall implementation.
- **Call-graph collapse**: before, nested boxes for the call tree. After, one box with the
  now-internal calls faded inside it.
