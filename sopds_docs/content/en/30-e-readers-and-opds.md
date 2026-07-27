# E-readers and OPDS

OPDS is a catalogue format e-reader apps understand. Point one at this library
and it can browse the whole collection, search it and download books directly to
the device.

## The addresses

| Feed | Address | Use it when |
|---|---|---|
| OPDS 1.2 (Atom) | `https://<this-site>/opds/` | Almost always. Every client speaks it. |
| OPDS 2.0 (JSON) | `https://<this-site>/opds/2.0/` | The client says it supports OPDS 2. |

Both serve the same collection. The 1.2 feed is not going away — most e-readers
still speak only that — and the 2.0 root is linked from it, so a client that
prefers JSON can find it on its own.

If the library requires a login, the feed asks for **HTTP Basic**
authentication: your usual username and password, which every OPDS client has a
field for.

## Setting up the usual clients

**KOReader** (Kobo, Kindle, PocketBook, Android)
: *Search → OPDS catalog → +* and enter the 1.2 address, your username and
  password. KOReader can also sync your reading position back here — see
  [Syncing reading progress](/docs/syncing-reading-progress/).

**Moon+ Reader** (Android)
: *Net Library → + → OPDS*, then the address and credentials.

**FBReader** (Android, desktop)
: *Network Library → + → Add catalog by URL*.

**Aldiko, KyBook, Marvin, Librera, PocketBook Reader**
: All have an "add OPDS catalogue by URL" of some sort; the address and
  credentials are the same.

**Calibre**
: *Get books → Search → Add a new source* accepts an OPDS URL, which is a
  convenient way to pull books into a desktop library.

## Searching from the device

The feed advertises an OpenSearch description, so the client's own search box
searches this catalogue rather than filtering what it has already downloaded.
In OPDS 2.0 the same thing is a templated `search` link.

## Downloads and conversion

A book is offered in the format it is stored in. Where the administrator has
configured the FB2 converters, FB2 books additionally appear as EPUB and MOBI
in the feed, converted as they are requested.

## If a client will not connect

- Check the address ends in `/opds/`, with the trailing slash.
- Use `https://`. Some clients silently refuse plain HTTP.
- If it asks for credentials repeatedly, the username or password is wrong;
  the same pair works on the web login page.
- Very old clients sometimes cannot handle a large first page. Browsing by
  author or genre rather than "all books" gets around it.
