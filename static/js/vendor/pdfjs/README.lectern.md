# pdf.js, as vendored here

Upstream: https://github.com/mozilla/pdf.js — `pdfjs-dist` 6.1.200, Apache
License 2.0 (`LICENSE` in this directory).

There is no build step in this project, so the prebuilt distribution is copied
in rather than pulled from npm. What is here and why:

| Path                 | Why it is needed                                                  |
|----------------------|-------------------------------------------------------------------|
| `pdf.min.mjs`        | The API. Loaded as an ES module by the paged reader.               |
| `pdf.worker.min.mjs` | Parsing and rasterising, off the main thread. Not optional.        |
| `standard_fonts/`    | The base-14 fonts. A PDF that references Helvetica or Times without embedding it renders with the wrong metrics — or not at all — when these are missing. |
| `wasm/`              | JBIG2, JPEG 2000, ICC. Scanned books use both codecs heavily; without these such a PDF shows blank pages. The `*_nowasm_fallback.js` files are upstream's pure-JS path for environments that refuse WebAssembly. |
| `iccs/`              | The predefined ICC profile pdf.js falls back to.                   |

The **legacy** build is taken rather than the modern one: it is ~13% larger and
runs on the older browsers that turn up on tablets and e-ink devices, which is
exactly the audience for reading a scan in the browser.

`cmaps/` is deliberately *not* vendored (1.7 MB). It is needed only for PDFs
using predefined CJK character collections. Copy `cmaps/` out of the same
release next to this file and set `cMapUrl` in `PagedReader.html` if a
CJK-heavy collection ever needs it.

## Upgrading

    npm pack pdfjs-dist@<version>
    tar xzf pdfjs-dist-<version>.tgz
    cp package/legacy/build/pdf.min.mjs package/legacy/build/pdf.worker.min.mjs .
    cp -r package/standard_fonts package/wasm package/iccs package/LICENSE .

Then check that `PagedReader.html` still passes the option names this version
expects (`workerSrc`, `standardFontDataUrl`, `wasmUrl`, `iccUrl`) — they have
been renamed across major versions before.
