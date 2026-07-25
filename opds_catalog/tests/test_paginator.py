"""OPDS_Paginator page-count edge cases (issue #41).

- num_pages must not add a spurious empty last page when the item count is an
  exact multiple of MAXITEMS.
- MAXITEMS coming from the admin-editable constance value must never make a
  page render ZeroDivisionError.
"""
import pytest

from opds_catalog.opds_paginator import Paginator


@pytest.mark.parametrize("count, maxitems, expected_pages", [
    (0, 60, 1),      # empty result still has one (empty) page
    (1, 60, 1),
    (60, 60, 1),     # exact multiple -> no extra empty page (was 2)
    (61, 60, 2),
    (120, 60, 2),
    (121, 60, 3),
])
def test_num_pages_no_spurious_last_page(count, maxitems, expected_pages):
    op = Paginator(count, 0, page_num=1, maxitems=maxitems)
    assert op.num_pages == expected_pages


def test_last_page_has_no_next_on_exact_multiple():
    op = Paginator(60, 0, page_num=1, maxitems=60)
    assert op.has_next is False


def test_zero_maxitems_does_not_raise():
    # SOPDS_MAXITEMS=0 must degrade, not ZeroDivisionError.
    op = Paginator(10, 0, page_num=1, maxitems=0)
    assert op.num_pages == 10
    assert op.MAXITEMS == 1
