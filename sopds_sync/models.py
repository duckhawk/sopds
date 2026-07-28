import hashlib

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone

from opds_catalog.models import Book


class KosyncCredential(models.Model):
    """Per-user credential for the KOReader "kosync" progress-sync protocol.

    KOReader never sends the plaintext password: it transmits md5(password) in
    the ``x-auth-key`` header on every request. We therefore cannot validate
    kosync against the Django/OIDC password — pbkdf2 and Keycloak's ROPC grant
    both need the plaintext, and md5 is one-way. Instead the user sets a
    dedicated *sync password* in the web UI; we store its md5 here and compare
    it against the incoming ``x-auth-key``. This keeps the real account password
    unexposed (a leak of this table only compromises the sync channel, which can
    be rotated on its own) and works uniformly for local and OIDC-only users.
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='kosync_credential')
    # md5 hex digest of the sync password (32 chars) — exactly what KOReader sends.
    auth_key = models.CharField(max_length=32)
    created = models.DateTimeField(default=timezone.now)

    @staticmethod
    def hash_password(password):
        """md5 of a plaintext password, matching KOReader's client-side hashing."""
        return hashlib.md5(password.encode('utf-8')).hexdigest()

    def set_password(self, password):
        self.auth_key = self.hash_password(password)

    def __str__(self):
        return 'kosync:%s' % self.user.username


class BookDigest(models.Model):
    """A KOReader document hash that identifies a catalogue Book.

    kosync names a book only by a hash the client computes from the copy on the
    device, so the protocol on its own cannot tell the server *which* book is
    being read. Precomputing the hashes our own files would produce closes that
    gap: :mod:`sopds_sync.digest` builds them and `sopds_kosync_index` stores
    them here.

    One book yields several: KOReader can hash either the file name or the
    contents, and the name on the device depends on how the book was fetched
    (``SOPDS_TITLE_AS_FILENAME`` picks between the transliterated title and the
    original name, and a zip download is extracted before it is read). Hence a
    table rather than a column, unique on the digest so a lookup is one index
    hit — and so two books that really do hash alike (the same file twice under
    different names) cannot both claim the hash.
    """
    FILENAME = 'filename'
    BINARY = 'binary'
    METHOD_CHOICES = [(FILENAME, 'File name md5'), (BINARY, 'Partial content md5')]

    book = models.ForeignKey(Book, db_index=True, on_delete=models.CASCADE,
                             related_name='digests')
    digest = models.CharField(max_length=32, unique=True)
    method = models.CharField(max_length=16, choices=METHOD_CHOICES)

    def __str__(self):
        return '%s:%s' % (self.method, self.digest)


class MoonReaderPosition(models.Model):
    """The last Moon+ Reader position marker exchanged for one book file.

    Moon+ Reader keeps reading positions as one small file per book in its cloud
    folder, so the state we have to track is per *file on the device*, not per
    catalogue book: `path` is where the marker lives in the user's DAV area and
    is what makes a write-back land where the phone will look for it. `name` is
    the book's file name there, which is all the protocol offers to identify
    what is being read; `book` is what we managed to match it to, and may be
    null for a book this catalogue does not hold.

    `rule` records which reading of Moon+ Reader's chapter numbering reproduced
    the marker's own percentage against our copy (see :mod:`sopds_sync.moonpos`).
    Empty means no reading did, which is the signal that the file on the phone
    is not the edition we have — progress still syncs as a percentage, but
    chapter coordinates must not be written back.
    """
    user = models.ForeignKey(User, db_index=True, on_delete=models.CASCADE)
    path = models.CharField(max_length=1024)
    name = models.CharField(max_length=512)
    book = models.ForeignKey(Book, null=True, default=None, db_index=True,
                             on_delete=models.SET_NULL)
    marker = models.CharField(max_length=64)
    rule = models.CharField(max_length=16, blank=True, default='')
    updated = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        unique_together = ['user', 'path']

    def __str__(self):
        return '%s:%s' % (self.user.username, self.name)


class KosyncProgress(models.Model):
    """One document's reading progress for a user — the kosync key-value store.

    ``document`` is the opaque 32-char hash KOReader computes on the client
    (either a filename-based or a partial-binary md5). Storage and retrieval do
    not depend on knowing which book that is: the latest progress reported for a
    ``(user, document)`` pair is kept and returned on GET, last write wins,
    matching the reference kosync server — conflict resolution is the client's
    job.

    ``book`` is resolved through :class:`BookDigest` when we recognise the hash.
    It is nullable and purely additive: it is what lets the web UI show that a
    book is being read on an e-reader, and it must never be able to make the
    sync protocol itself fail.
    """
    user = models.ForeignKey(User, db_index=True, on_delete=models.CASCADE)
    document = models.CharField(max_length=32, db_index=True)
    book = models.ForeignKey(Book, null=True, default=None, db_index=True,
                             on_delete=models.SET_NULL)
    progress = models.CharField(max_length=1024)  # xpointer / page position, opaque to us
    percentage = models.FloatField(default=0.0)
    device = models.CharField(max_length=256, blank=True, default='')
    device_id = models.CharField(max_length=256, blank=True, default='')
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        unique_together = ['user', 'document']

    def __str__(self):
        return '%s:%s' % (self.user.username, self.document)
