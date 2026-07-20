#######################################################################
#
# Вспомогательные функции
#
import unicodedata

from types import SimpleNamespace

from django.core.cache import cache
from django.db import connection

from constance import config


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
