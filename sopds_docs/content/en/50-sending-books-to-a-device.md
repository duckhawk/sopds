# Sending a book to a device

Some readers have no OPDS client at all. A Kindle is the obvious one: the way
to get a book onto it is to mail the file to the address Amazon gave it.
**Send** on a book card does that.

## Setting it up

1. In **Settings**, fill in **Address of your reading device** — your
   `@kindle.com` address, or any mailbox.
2. For a Kindle, add this library's sending address to your **approved senders**
   list in the Amazon account settings. Amazon silently discards mail from
   anywhere else. The sending address is the one the administrator configured;
   ask them if you do not know it.
3. **Send** now appears on every book.

If **Send** is missing, either you have not set an address or the server has no
mail configured.

## What gets sent

The original file, as an attachment, with the book's title as the subject —
Amazon uses the subject as the document title when it converts, and ignores the
message body.

Two limits worth knowing:

- **25 MB.** Larger books are refused up front with a message rather than
  vanishing somewhere in a mail relay.
- **FB2 and DjVu are not formats a Kindle accepts.** You are warned but not
  stopped, because the destination may not be a Kindle. For an FB2, downloading
  it as EPUB first (where the administrator has set up the converter) is the
  way round it.

Nothing else is converted on the way out — what is in the library is what
arrives.
