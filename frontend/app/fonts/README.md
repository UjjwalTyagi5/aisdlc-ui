# Vendored web fonts

These `.woff2` files are checked in so the app builds and renders correctly with
no network access. Previously `app/layout.tsx` used `next/font/google`, which
fetches `fonts.googleapis.com` on every cold compile; offline that fails and
Next silently substitutes a system fallback, changing the whole UI's typography.

| File | Family | Axes | Source |
| --- | --- | --- | --- |
| `BricolageGrotesque-latin-var.woff2` | Bricolage Grotesque v9 | `opsz` 12..96, `wght` 200..800 | Google Fonts |
| `HankenGrotesk-latin-var.woff2` | Hanken Grotesk v12 | `wght` 100..900 | Google Fonts |
| `JetBrainsMono-latin-var.woff2` | JetBrains Mono v24 | `wght` 100..800 | Google Fonts |

All three are variable fonts, `latin` subset only — the same subset the previous
`subsets: ["latin"]` config requested. All are licensed under the SIL Open Font
License 1.1, which permits redistribution alongside our source.

## Refreshing a font

Fetch the CSS with a browser user-agent (Google serves `woff2` only to modern
UAs), take the `src` URL from the `/* latin */` block, and download it:

```sh
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
curl -A "$UA" "https://fonts.googleapis.com/css2?family=Hanken+Grotesk:wght@100..900&display=swap"
curl -A "$UA" "<latin src url from above>" -o HankenGrotesk-latin-var.woff2
```

Request a weight *range* (`wght@100..900`), not a list (`wght@400;500;600;700`) —
a list returns static instances, one file per weight.
