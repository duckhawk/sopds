# Getting started

Lectern is a catalogue for a library of e-books. It keeps track of what is in a
directory of files, lets you find things in it from a browser, and serves the
same collection over **OPDS** so an e-reader can browse and download books
without a cable.

It is a fork of [SOPDS](http://www.sopds.ru/) that has grown a fair distance
from it: an in-browser reader for four formats, reading-progress sync with
e-reader apps, OPDS 2.0 alongside the classic feed, tags, shared lists, ratings
and sending books by mail.

## Finding a book

The search box at the top searches **titles** by default; the control at its
right edge switches it to authors, series or genres. Typing offers suggestions
after the third character.

When you would rather browse than search, the menu has the usual ways in:

- **Recently added** — what the last scan of the collection found.
- **Top rated** and **Most popular** — by readers' ratings and by how often a
  book has been downloaded or opened here.
- **Books**, **Authors**, **Series** — alphabetical, split by language.
- **Genres** and **Catalogs** — by subject, and by the directory tree the files
  actually live in.
- **Tags** and **Lists** — see [Organising the library](/docs/organising-the-library/).

## What you can do with a book

Every book on a listing page has the same set of actions:

- **Read** — open it in the browser. Available for FB2, EPUB, PDF and DjVu; see
  [Reading in the browser](/docs/reading-in-the-browser/).
- **Download** — the original file, or a zip of it. Where the administrator has
  set up converters, FB2 can also be downloaded as EPUB or MOBI.
- **Send** — mail it to your reading device; see
  [Sending a book to a device](/docs/sending-books-to-a-device/).
- **Shelf**, **status**, **rating**, **tags**, **lists** — your own record of
  what you have read and what you thought of it.

## Your account

Most of the personal features — the shelf, statuses, ratings, lists, saved
reading position, progress sync — need you to be signed in, because they are
about you rather than about the book.

Where the administrator has allowed it you can create an account yourself from
the login page; otherwise ask them for one. A forgotten password can be reset
by mail if the server has mail configured. Some installations sign you in
through Keycloak instead, in which case the login page offers a button for it.

**Settings** (in the top bar, once signed in) holds the light/dark theme, the
reader's font size and mode, and the address books are sent to.
