# Syncing reading progress

Read half a book on the sofa, pick up a different device, and carry on from the
same page. Two protocols are supported, both configured from **Device sync** in
the top bar once you are signed in.

They only sync a *position*, not the book. The device downloads the book from
the catalogue as usual; the sync tells it where you got to.

## KOReader

KOReader's own protocol, "kosync". In KOReader:

*Tools → Progress sync → Custom sync server*

- **Server**: `https://<this-site>/kosync/`
- **Username**: your Lectern username
- **Password**: a **separate sync password**, set on the Device sync page

Then choose **Login**, not Register.

The separate password is deliberate. KOReader stores it and sends it as an
unsalted MD5 hash, which is not something to do with the password that also
opens your account here. Set one on the Device sync page and use that.

KOReader identifies a book by a digest of the file's contents, not by its name,
so the position follows the book even if it is renamed — and a copy of the same
book downloaded from somewhere else is recognised as the same book. The
catalogue builds an index of these digests as it scans; a book added since the
last scan will not be recognised until the next one.

## Moon+ Reader Pro

Moon+ syncs through WebDAV. In Moon+ Reader:

*Options → Sync → WebDAV*

- **URL**: `https://<this-site>/dav/`
- **Username** and **password**: your ordinary Lectern account

No separate password here — WebDAV sends the credentials properly, so the usual
one is fine. Each account gets its own private storage; nobody can see anyone
else's.

## The reading position, in three places

Something worth knowing, because it explains what syncs where:

- The **in-browser reader** saves a paragraph (or page) number. It is exact,
  and it means nothing outside this site.
- **kosync** and **WebDAV** exchange a *percentage*, which is what the
  protocols carry.
- The percentage is also what shows on the book card and what moves a book to
  "Reading" or "Finished" automatically.

So progress from an e-reader shows up here as a percentage, and the browser
reader's own position is separate and finer-grained. They do not fight; they
answer different questions.

## If it is switched off

Both endpoints are disabled by default. The Device sync page will say so, and
an administrator has to turn on `SOPDS_KOSYNC_ENABLE` or `SOPDS_WEBDAV_ENABLE`.
