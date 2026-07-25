"""findbook/findcat must tolerate duplicate rows instead of raising
MultipleObjectsReturned and aborting the scan (#37).
"""
import pytest

from opds_catalog import opdsdb
from opds_catalog.models import Book, Catalog


@pytest.mark.django_db
def test_findbook_with_duplicates_does_not_raise():
    cat = opdsdb.addcattree('.')
    opdsdb.addbook('a.fb2', '.', cat, 'fb2', 'T', '', '2020', 'en', 1, 0)
    opdsdb.addbook('a.fb2', '.', cat, 'fb2', 'T', '', '2020', 'en', 1, 0)
    assert Book.objects.filter(filename='a.fb2', path='.').count() == 2

    book = opdsdb.findbook('a.fb2', '.')   # would MultipleObjectsReturned with .get()
    assert book is not None


@pytest.mark.django_db
def test_findcat_with_duplicates_does_not_raise():
    Catalog.objects.create(parent=None, cat_name='p', path='p', cat_type=0)
    Catalog.objects.create(parent=None, cat_name='p', path='p', cat_type=0)

    cat = opdsdb.findcat('p')
    assert cat is not None
