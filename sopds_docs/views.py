# -*- coding: utf-8 -*-
"""Serving the documentation.

Deliberately outside the login wall, even where the catalogue is not: these
pages describe how to connect a reader and what the site does, which is exactly
what someone who cannot get in yet needs, and they contain nothing about the
library itself.
"""
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET
from django.views.decorators.vary import vary_on_headers

from sopds_docs import pages


def _args(request, slug, lang):
    from sopds_web_backend.views import theme_css

    return {
        'css_file': theme_css(request.user),
        'current': 'docs',
        'pages': pages.index(lang),
        'slug': slug,
        'breadcrumbs': [_('Documentation')],
    }


@vary_on_headers('HTTP_ACCEPT_LANGUAGE')
@require_GET
def index(request):
    """/docs/ — the first section, rather than a page listing the sections.

    A contents page whose only content is links to four other pages wastes the
    reader's first click.
    """
    slug = pages.first_slug()
    if slug is None:
        raise Http404
    return redirect('docs:page', slug=slug)


@vary_on_headers('HTTP_ACCEPT_LANGUAGE')
@require_GET
def page(request, slug):
    html, lang = pages.render(slug)
    if html is None:
        raise Http404

    args = _args(request, slug, lang)
    args['body'] = html
    # Only worth saying when the reader asked for one language and got another.
    args['fallback'] = lang != pages.language()
    for page_slug, title in args['pages']:
        if page_slug == slug:
            args['title'] = title
            args['breadcrumbs'] = [_('Documentation'), title]
            break
    return render(request, 'sopds_docs.html', args)
