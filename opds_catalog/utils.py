#######################################################################
#
# Вспомогательные функции
#

from types import SimpleNamespace

from django.core.cache import cache
from django.db import connection

from constance import config


def contains_page_ids(table, column, term, select_cols, order_by, limit, offset):
    """Return a page of row ids where `column LIKE '%term%'`, ordered by
    `order_by`, using an OFFSET-0 fence subquery.

    On PostgreSQL an ``ORDER BY`` combined with a leading-wildcard ``LIKE``
    makes the planner drive the query off the plain btree index and filter
    row-by-row, ignoring the pg_trgm GIN index (very slow on large, cyrillic
    catalogs). Wrapping the filter in a ``(... OFFSET 0)`` fence forces the
    trigram bitmap scan first and only then sorts the (small) match set.

    `table`, `column`, `select_cols` and `order_by` are fixed identifiers
    supplied by our own callers — never user input (`select_cols` must list
    every column referenced by `order_by`). `term` is a bound parameter with
    its LIKE wildcards escaped.
    """
    escaped = term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    pattern = '%' + escaped + '%'
    if connection.vendor == 'postgresql':
        # The OFFSET 0 fence forces the pg_trgm bitmap scan to run (and the row
        # set to materialise) before the ORDER BY sort. (PostgreSQL only —
        # SQLite rejects OFFSET without LIMIT, and has no trigram index anyway.)
        sql = (
            "SELECT id FROM ("
            "  SELECT id, {select_cols} FROM {tbl} WHERE {col} LIKE %s ESCAPE '\\' OFFSET 0"
            ") t ORDER BY {order} LIMIT %s OFFSET %s"
        )
    else:
        sql = (
            "SELECT id FROM {tbl} WHERE {col} LIKE %s ESCAPE '\\' "
            "ORDER BY {order} LIMIT %s OFFSET %s"
        )
    sql = sql.format(tbl=table, col=column, select_cols=select_cols, order=order_by)
    with connection.cursor() as cursor:
        cursor.execute(sql, [pattern, limit, offset])
        return [row[0] for row in cursor.fetchall()]


def contains_page(base_qs, table, column, term, select_cols, order_by, limit, offset):
    """Hydrate one ordered page of `base_qs` rows matching `column LIKE
    '%term%'` via the trgm-friendly fenced id query (see contains_page_ids).

    `base_qs` is the model queryset to load from (already carrying any needed
    annotations/prefetch); the page is returned in `order_by` order.
    """
    ids = contains_page_ids(table, column, term, select_cols, order_by, limit, offset)
    by_id = {obj.id: obj for obj in base_qs.filter(id__in=ids)}
    return [by_id[i] for i in ids if i in by_id]


def alphabet_menu(table, column, lang_code, chars):
    """Alphabet drill-down counts for the "select by substring" pages/feeds.

    For every distinct prefix of length len(chars)+1 that starts with `chars`,
    return the number of matching rows as SimpleNamespace(id=<prefix>, cnt=<n>,
    l=<prefix length>). Attribute access works both in templates and in Python
    (OPDS feeds), and the objects pickle fine for caching.

    The result depends only on (table, column, lang_code, chars) and changes
    only after a library scan, so it is cached for SOPDS_CACHE_TIME. This turns
    a full-table GROUP BY over the whole books/authors/series table (previously
    run on every page load) into a single cached computation.

    `chars`/`lang_code` are user-controlled and passed as bound parameters,
    which also closes the SQL injection that raw string interpolation allowed.
    `table`/`column`/`length` come only from our own callers (fixed identifiers).
    """
    length = len(chars) + 1
    cache_key = 'alphabet:%s:%s:%s:%s' % (table, lang_code, length, chars)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    where = []
    params = []
    if lang_code:
        where.append('lang_code = %s')
        params.append(lang_code)
    # Escape LIKE wildcards so a literal % or _ in a prefix matches literally.
    escaped = chars.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    where.append("%s LIKE %%s ESCAPE '\\'" % column)
    params.append(escaped + '%')
    where_sql = ' and '.join(where)

    sql = (
        "select substr({col}, 1, {length}) as id, count(*) as cnt "
        "from {tbl} where {where} "
        "group by substr({col}, 1, {length}) order by id"
    ).format(col=column, length=length, tbl=table, where=where_sql)

    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        result = [SimpleNamespace(id=row[0], cnt=row[1], l=length) for row in cursor.fetchall()]

    cache.set(cache_key, result, config.SOPDS_CACHE_TIME)
    return result

def translit(s):
    """Russian translit: converts 'привет'->'privet'"""
    assert s is not str, "Error: argument MUST be string"

    table1 = str.maketrans("абвгдеёзийклмнопрстуфхъыьэАБВГДЕЁЗИЙКЛМНОПРСТУФХЪЫЬЭ",  "abvgdeezijklmnoprstufh'y'eABVGDEEZIJKLMNOPRSTUFH'Y'E")
    table2 = {'ж':'zh','ц':'ts','ч':'ch','ш':'sh','щ':'sch','ю':'ju','я':'ja',  'Ж':'Zh','Ц':'Ts','Ч':'Ch','Ш':'Sh','Щ':'Sch','Ю':'Ju','Я':'Ja', 
              '«':'', '»':'','"':'','\n':'_',' ':'_',"'":"",':':'_','№':'N'}
    s = s.translate(table1)
    for k in table2.keys():
        s = s.replace(k,table2[k])
    return s

def to_ascii(s):
    return s.encode('ascii', 'replace').decode('utf-8')
