import hashlib

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone


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


class KosyncProgress(models.Model):
    """One document's reading progress for a user — the kosync key-value store.

    ``document`` is the opaque 32-char hash KOReader computes on the client
    (either a filename-based or a partial-binary md5). The server never needs to
    map it to a catalog Book: it just stores the latest progress reported for a
    ``(user, document)`` pair and returns it on GET. Last write wins, matching
    the reference kosync server — conflict resolution is the client's job.
    """
    user = models.ForeignKey(User, db_index=True, on_delete=models.CASCADE)
    document = models.CharField(max_length=32, db_index=True)
    progress = models.CharField(max_length=1024)  # xpointer / page position, opaque to us
    percentage = models.FloatField(default=0.0)
    device = models.CharField(max_length=256, blank=True, default='')
    device_id = models.CharField(max_length=256, blank=True, default='')
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        unique_together = ['user', 'document']

    def __str__(self):
        return '%s:%s' % (self.user.username, self.document)
