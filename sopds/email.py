# -*- coding: utf-8 -*-
"""SMTP configured from the admin rather than from settings.

Everything else an operator has to set here — the OIDC client, the Telegram
token, the sync toggles, the metrics token — lives in constance and takes effect
without a restart. Mail is configured the same way, so a password reset that
does not arrive can be diagnosed and fixed in the admin rather than through a
redeploy.

Django reads `EMAIL_HOST` and friends from settings when a connection is opened,
so making them dynamic means a backend that supplies them itself. This is that
backend: the standard SMTP one with its parameters filled in from constance at
connection time.
"""
import logging

from django.core.mail.backends.smtp import EmailBackend as SMTPBackend

from constance import config

logger = logging.getLogger(__name__)


def is_configured():
    """Whether enough is set for mail to have any chance of being delivered.

    Callers use this to hide features that would otherwise fail silently: an
    offer to email a book to a device is worse than no offer at all if nothing
    can send it.
    """
    return bool((config.SOPDS_SMTP_HOST or '').strip()
                and (config.SOPDS_MAIL_FROM or '').strip())


def from_address():
    return (config.SOPDS_MAIL_FROM or '').strip()


class ConstanceEmailBackend(SMTPBackend):
    """`EmailBackend`, with host, port and credentials read from constance."""

    def __init__(self, host=None, port=None, username=None, password=None,
                 use_tls=None, use_ssl=None, **kwargs):
        # Explicit arguments still win, so a test or a management command can
        # pass its own; otherwise the admin's values are used.
        super().__init__(
            host=host if host is not None else (config.SOPDS_SMTP_HOST or '').strip(),
            port=port if port is not None else config.SOPDS_SMTP_PORT,
            username=username if username is not None else (config.SOPDS_SMTP_USER or '').strip(),
            password=password if password is not None else (config.SOPDS_SMTP_PASSWORD or ''),
            use_tls=use_tls if use_tls is not None else config.SOPDS_SMTP_TLS,
            use_ssl=use_ssl if use_ssl is not None else config.SOPDS_SMTP_SSL,
            **kwargs)
