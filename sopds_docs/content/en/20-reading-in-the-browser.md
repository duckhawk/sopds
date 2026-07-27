# Reading in the browser

Press **Read** on a book and it opens here, with no reader app and nothing
downloaded. Four formats are supported, and they are shown by two different
readers, because they are two different kinds of book.

## Text that reflows: FB2 and EPUB

FB2 and EPUB are text, so the browser lays them out to fit whatever you are
reading on. The server renders the whole book to one long page and the reader
remembers where you were.

- **Font size** and **whole text / by chapters** are in **Settings**. In
  chapter mode the arrows at the bottom right turn one section at a time.
- The bar along the bottom of the window is how far through you are.
- Your position is saved as you scroll and restored when you come back —
  including when you come back from a different device.
- The rendered book is kept in the browser's local storage after the first
  visit, so re-opening it does not fetch it again.

Illustrations inside an EPUB are served from the book itself; nothing is
extracted onto the server.

## Pages of ink: PDF and DjVu

A scan has no paragraphs to reflow, only pages, so these open in a page-based
reader that draws them one at a time.

- Pages are drawn as they come into view, so a long book opens immediately
  rather than after the whole file has arrived.
- The buttons at the bottom right zoom in and out, and reset to fitting the
  page width.
- The page number is saved and restored the same way a paragraph is in the
  other reader.

**PDF** works everywhere. **DjVu** is converted to PDF on the server the first
time it is opened, which needs `ddjvu` (from djvulibre) installed — where it is
not, DjVu books are still listed and downloadable, but **Read** is not offered
for them. The conversion is cached, so only the first reader of a given book
waits for it; a large scan of photographic pages can take a little while that
first time.

## What is not offered

**MOBI** cannot be read here. It is a binary container aimed at one family of
devices; download it, or send it to a device that understands it.

Text selection and search inside a PDF are not implemented — the paged reader
draws pages and does not build a text layer over them.
