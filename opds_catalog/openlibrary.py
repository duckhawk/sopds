# -*- coding: utf-8 -*-
#
# Minimal Open Library Books API client, used by the `sopds_enrich` command to
# fill in metadata the book files themselves do not carry.
#
# https://openlibrary.org/dev/docs/api/books
import logging

import requests

logger = logging.getLogger(__name__)

API_URL = 'https://openlibrary.org/api/books'

# Open Library asks callers to identify themselves so they can contact the
# operator of a misbehaving client instead of blocking it outright.
USER_AGENT = 'Lectern-OPDS/1.0 (+https://github.com/mitshel/sopds) metadata enrichment'

# The API takes many bibkeys per call. Keep the batch well under the point where
# the query string gets unwieldy; 50 ISBNs is ~750 characters.
MAX_BATCH = 50

TIMEOUT = 20


def _text(value):
    """Open Library returns a description either as a plain string or as
    {'type': '/type/text', 'value': ...}."""
    if isinstance(value, dict):
        value = value.get('value', '')
    return value.strip() if isinstance(value, str) else ''


def parse_details(details):
    """Pull the fields we can store out of one book's `details` object.

    Returns a dict with only the keys that had a usable value, so a caller can
    tell "Open Library has no publisher" from "the publisher is empty string".
    """
    out = {}

    description = _text(details.get('description'))
    if description:
        out['annotation'] = description

    publishers = details.get('publishers') or []
    if publishers and isinstance(publishers[0], str):
        out['publisher'] = publishers[0].strip()

    publish_date = details.get('publish_date')
    if isinstance(publish_date, str) and publish_date.strip():
        out['docdate'] = publish_date.strip()

    return out


def fetch(isbns, session=None, api_url=API_URL, timeout=TIMEOUT):
    """Look up a batch of ISBNs; return {isbn: {field: value}} for those found.

    Returns None — distinct from an empty dict — when the request itself failed.
    One bad batch must not abort a run over a large catalogue, so the error is
    logged rather than raised; but the caller has to be able to tell "Open
    Library does not know these books" from "we never got to ask", or a
    momentary outage would permanently mark the batch as looked up.
    """
    isbns = [i for i in isbns if i]
    if not isbns:
        return {}

    params = {
        'bibkeys': ','.join('ISBN:%s' % i for i in isbns),
        'jscmd': 'details',
        'format': 'json',
    }
    get = (session or requests).get

    try:
        response = get(api_url, params=params, timeout=timeout,
                       headers={'User-Agent': USER_AGENT})
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as err:
        logger.warning('Open Library lookup failed for %d ISBNs: %s', len(isbns), err)
        return None

    if not isinstance(payload, dict):
        logger.warning('Open Library returned %s, expected an object', type(payload).__name__)
        return None

    result = {}
    for key, entry in payload.items():
        isbn = key.split(':', 1)[-1]
        details = (entry or {}).get('details') or {}
        fields = parse_details(details)
        if fields:
            result[isbn] = fields

    return result
