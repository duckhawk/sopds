# -*- coding: utf-8 -*-
"""Mailing a book to a reading device.

The classic missing path. An e-reader without an OPDS client — a Kindle, most
obviously — can only be filled by sending files to its address, and until now
the only way to get a book out of this catalogue was to download it in a
browser and move it across by hand.

Amazon's send-to-Kindle accepts a document as an attachment from an address the
owner has approved. Nothing here is Kindle-specific though: it mails a book to
whatever address the reader configured, which is equally what Pocketbook, Kobo
and a plain mailbox want.
"""
import logging

from django.core.mail import EmailMessage, get_connection
from django.utils.translation import gettext as _


from opds_catalog import dl
from opds_catalog.models import Theme
from book_tools.format import mime_detector
from sopds import email as mail

logger = logging.getLogger(__name__)

# Mail systems reject large attachments, and Amazon's limit has historically
# been well under this. Refusing up front with a clear reason beats a message
# that vanishes somewhere in a relay.
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024

# Formats a Kindle will not take. Not enforced — the destination may not be a
# Kindle at all — but worth warning about, since a rejected message usually
# bounces into a mailbox nobody reads.
KINDLE_UNSUPPORTED = ('fb2', 'djvu')


class DeliveryError(Exception):
    """Sending did not happen, with a reason fit to show a reader."""


def device_email(user):
    """The address this reader configured, or ''."""
    prefs = Theme.objects.filter(user=user).first()
    return (prefs.device_email if prefs else '') or ''


def can_send(user):
    """Whether offering to send a book to this reader makes any sense."""
    return bool(mail.is_configured() and device_email(user))


def send(user, book, address=None):
    """Mail one book to the reader's device. Raises DeliveryError on refusal."""
    if not mail.is_configured():
        raise DeliveryError(_('Mail is not configured on this server.'))

    address = (address or device_email(user)).strip()
    if not address:
        raise DeliveryError(_('You have not set a device address yet.'))

    data = dl.getFileData(book)
    if data is None:
        raise DeliveryError(_('The file for this book is missing.'))

    payload = data.read()
    if len(payload) > MAX_ATTACHMENT_BYTES:
        raise DeliveryError(
            _('This book is too large to send (%(size)d MB; the limit is %(limit)d MB).')
            % {'size': len(payload) // (1024 * 1024),
               'limit': MAX_ATTACHMENT_BYTES // (1024 * 1024)})

    filename = dl.getFileName(book)
    message = EmailMessage(
        # Amazon uses the subject as the document title when it converts, and
        # ignores the body entirely.
        subject=book.title or filename,
        body='',
        from_email=mail.from_address(),
        to=[address],
        connection=get_connection(),
    )
    message.attach(filename, payload, str(mime_detector.fmt(book.format)))

    try:
        message.send(fail_silently=False)
    except Exception as err:
        # The reason belongs in the log, not in the page: it can name the relay
        # and the credentials it rejected.
        logger.exception('Could not send book %s to a device', book.id)
        raise DeliveryError(_('The server could not send the message.')) from err

    logger.info('Sent book %s to a device for user %s', book.id, user.pk)
    return address


def warning_for(book):
    """A caveat worth showing next to the button, or None."""
    if book.format in KINDLE_UNSUPPORTED:
        return _('A Kindle will not accept %(format)s. Convert it first, or send '
                 'it to a device that reads this format.') % {'format': book.format}
    return None
