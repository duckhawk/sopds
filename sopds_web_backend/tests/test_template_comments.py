"""Comments in templates must actually be comments.

Django's `{# ... #}` is single-line only: its lexer never looks past the end of
the line, so a comment written across two lines is not a comment at all. It is
emitted into the page verbatim, and it looks fine in the source — which is why
this has now happened twice, once in the OpenSearch descriptor and once above
the login form, where visitors were reading a note addressed to the
administrator.

`{% comment %} ... {% endcomment %}` spans lines. This walks every template in
the repository rather than the handful currently known to be wrong.
"""
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Third-party templates are not ours to fix.
SKIP = ('.venv', 'node_modules', 'site-packages', os.path.join('constance', ''))


def templates():
    for base, dirs, names in os.walk(ROOT):
        if any(part in base for part in SKIP):
            continue
        dirs[:] = [d for d in dirs if d not in ('.git', '.venv', 'node_modules')]
        for name in names:
            if name.endswith('.html'):
                yield os.path.join(base, name)


def spans_lines(text, start):
    """True if the `{#` at `start` has no `#}` before the end of its line."""
    close = text.find('#}', start)
    line_end = text.find('\n', start)
    return close == -1 or (line_end != -1 and close > line_end)


def offenders():
    found = []
    for path in templates():
        with open(path, encoding='utf-8', errors='replace') as f:
            text = f.read()
        for match in re.finditer(r'\{#', text):
            if spans_lines(text, match.start()):
                line = text[:match.start()].count('\n') + 1
                found.append('%s:%d' % (os.path.relpath(path, ROOT), line))
    return found


def test_no_template_comment_spans_more_than_one_line():
    found = offenders()
    assert not found, (
        'Django {# #} comments are single-line; these are emitted into the page. '
        'Use {%% comment %%}...{%% endcomment %%}: %s' % ', '.join(found))


@pytest.mark.django_db
def test_the_login_page_does_not_leak_the_note_above_its_form(client):
    """The one that got out."""
    from constance import config
    config.SOPDS_AUTH = True
    config.SOPDS_LOGIN_NOTICE = 'Log in as demo / demo.'

    body = client.get('/web/login/').content.decode()
    assert 'Log in as demo / demo.' in body
    assert 'the administrator' not in body
    assert '{#' not in body and '#}' not in body


@pytest.mark.django_db
@pytest.mark.parametrize('path', ['/web/', '/web/login/', '/web/register/', '/docs/'])
def test_no_page_renders_a_comment_marker(client, db, path):
    from constance import config
    config.SOPDS_AUTH = False
    config.SOPDS_ALLOW_REGISTRATION = True

    body = client.get(path, follow=True).content.decode()
    assert '{#' not in body and '#}' not in body, path


def test_the_check_would_have_caught_it():
    """A guard nobody has seen fail is a guard nobody should trust."""
    broken = 'a {# one\nline too many #} b'
    assert spans_lines(broken, broken.index('{#'))
    fine = 'a {# all on one line #} b'
    assert not spans_lines(fine, fine.index('{#'))
