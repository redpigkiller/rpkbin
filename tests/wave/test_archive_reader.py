"""Raw Log history keeps scanning, payloads and cursor scopes bounded."""

import pytest

from rpkbin.wave import RunArchive
from rpkbin.wave import _archive_format as archive_module


@pytest.fixture
def store(tmp_path):
    archive = RunArchive(tmp_path / 'reader.sqlite3', create=True)
    archive.save_attempt(dict(id='j', attempt_id='a', attempt_number=1))
    yield archive
    archive.close()


def append(store, seq, text):
    store.append('a', dict(line_no=seq, time=float(seq), source='stdout', text=text),
                 float(seq))


def walk(store, **options):
    cursor = None
    while True:
        page = store.reader('j', 'a', cursor=cursor, **options)
        yield from page['rows']
        if not page['has_more']:
            break
        assert page['next'] and page['next'] != cursor
        cursor = page['next']


def test_empty_log_rows_and_end_of_text_chunks_remain_readable(store):
    append(store, 1, '')
    append(store, 2, 'é')
    store.flush()
    for rows in (store.page('j', 'a')['rows'], list(walk(store))):
        assert [row['text'] for row in rows] == ['', 'é']
    empty = store.read_text('j', 'a', 1)
    assert empty['text'] == '' and empty['total'] == 0 and not empty['has_more']
    end = store.read_text('j', 'a', 2, offset=2)
    assert end['text'] == '' and end['next_offset'] == 2 and not end['has_more']


def test_sparse_fulltext_search_continues_through_empty_batches(store, monkeypatch):
    monkeypatch.setattr(archive_module, 'READER_SCAN_LIMIT', 7, raising=False)
    for seq in range(1, 25):
        append(store, seq, ('x' * 1500 + ' NeedLe') if seq == 22 else 'other')
    first = store.reader('j', 'a', query='needle')
    assert first['rows'] == [] and first['has_more']
    assert first['scanned'] == 7 and first['scanned_seq'] == 7
    assert first['end_seq'] == 24 and first['next']
    matches = list(walk(store, query='needle'))
    assert [row['seq'] for row in matches] == [22]
    assert matches[0]['truncated']  # Matching reads beyond bounded display preview.


def test_bidirectional_windows_and_scope_bound_cursors(store):
    for seq in range(1, 5):
        append(store, seq, 'state=RUN')
    rows = list(walk(store, limit=1))
    assert [row['seq'] for row in rows] == [1, 2, 3, 4]
    reverse = list(walk(store, limit=1, direction='backward'))
    assert [row['seq'] for row in reverse] == [4, 3, 2, 1]
    page = store.reader('j', 'a', limit=1)
    assert len(page['last']) < 200
    assert store.reader('j', 'a', cursor=page['last'])['rows'][0]['seq'] == 2
    with pytest.raises(ValueError, match='Cursor'):
        store.reader('j', 'a', query='RUN', cursor=page['last'])
    middle = store.reader('j', 'a', anchor=2)
    back = store.reader('j', 'a', cursor=middle['first'], direction='backward')
    assert [row['seq'] for row in back['rows']] == [1]


def test_raw_text_is_not_reinterpreted_as_parsed_business_data(store):
    text = '{"kind":"tool","x":9007199254740993,"values":[null,true]}'
    append(store, 1, text)
    row = store.reader('j', 'a')['rows'][0]
    assert row['text'] == text and row['source'] == 'stdout'
    assert not any(key in row for key in ('name', 'value', 'previous', 'changed'))
    assert not hasattr(store, 'statistics')
    with pytest.raises(TypeError):
        store.reader('j', 'a', fields=['x'])
    with pytest.raises(TypeError):
        store.read_text('j', 'a', 1, field='x')


def test_regex_validation_timeout_and_no_silent_record_skip(store):
    append(store, 1, 'state=RUN ' + 'a' * 200000 + '!')
    assert store.reader('j','a',query='state|aagc',regex=True)['rows'][0]['seq'] == 1
    with pytest.raises(ValueError, match='regex|Regex|regular expression'):
        store.reader('j','a',query='[',regex=True)
    with pytest.raises(ValueError, match='regular expression'):
        store.reader('j','a',query='(' * 1000,regex=True)
    with pytest.raises(ValueError, match='timed out|timeout'):
        store.reader('j','a',query='(a+)+$',regex=True)
    with pytest.raises(ValueError):
        store.reader('j','a',query='x' * 2049)
    append(store,2,'x' * (8*1024*1024+1))
    with pytest.raises(ValueError, match='large|MiB|limit'):
        store.reader('j','a',query='absent')


def test_anchor_preserves_whitespace_and_long_raw_text_is_chunked(store):
    text = '  a\tb    ' + '中😀' * 1000
    append(store, 1, text)
    append(store, 2, 'next')
    logs = store.reader('j', 'a')['rows']
    assert text.startswith(logs[0]['text']) and logs[0]['truncated']
    assert logs[1]['text'] == 'next'
    assert [r['seq'] for r in store.reader('j', 'a', anchor=2, direction='backward')['rows']] == [1, 2]
    chunks, offset = [], 0
    while True:
        chunk = store.read_text('j', 'a', 1, offset=offset, limit=101)
        chunks.append(chunk['text'])
        offset = chunk['next_offset']
        if not chunk['has_more']:
            break
    assert ''.join(chunks) == text
    assert offset == len(text.encode('utf-8'))


def test_time_budget_and_payload_budget_produce_reusable_continuations(store, monkeypatch):
    for seq in range(1, 5):
        append(store, seq, 'row')
    monkeypatch.setattr(archive_module, 'READER_SECONDS', 0)
    page = store.reader('j','a')
    assert page['scanned'] == 1 and page['has_more']
    assert [r['seq'] for r in walk(store)] == [1,2,3,4]
    monkeypatch.setattr(archive_module, 'READER_SECONDS', 1)
    monkeypatch.setattr(archive_module, 'PAGE_BYTES', 300)
    pages = list(walk(store))
    assert [r['seq'] for r in pages] == [1,2,3,4]
    with pytest.raises(ValueError, match='page payload'):
        store.append('a', dict(line_no=5, time=5., text='row', source='n' * 1000), 5.)
        list(walk(store))


def test_reader_search_avoids_n_plus_one_queries_and_preserves_surrogatepass(tmp_path, monkeypatch):
    from contextlib import contextmanager

    store = RunArchive(tmp_path / 'single_query.sqlite3', create=True)
    store.save_attempt(dict(id='j', attempt_id='a', attempt_number=1))
    try:
        for seq in range(1, 31):
            text = f'row-{seq}-\ud800-match' if seq in (5, 25) else f'row-{seq}-plain'
            append(store, seq, text)
        store.flush()

        statements = []
        original_reader = store._reader

        @contextmanager
        def traced_reader():
            with original_reader() as conn:
                conn.set_trace_callback(statements.append)
                try:
                    yield conn
                finally:
                    conn.set_trace_callback(None)

        monkeypatch.setattr(store, '_reader', traced_reader)
        page = store.reader('j', 'a', query='match')
        assert [r['seq'] for r in page['rows']] == [5, 25]
        assert '\ud800' in page['rows'][0]['text']
        select_texts = [sql for sql in statements if 'FROM records' in sql]
        assert len(select_texts) == 2  # end_seq query + single candidate scan query
    finally:
        store.close()


def test_reader_case_sensitive_whole_word_and_regex_combinations(store):
    lines = [
        (1, 'run running RUN runner'),
        (2, 'state=Run error=0'),
        (3, 'state=RUNNING error=1'),
        (4, 'err ERR error Errors'),
        (5, 'metric a.b=1 and aXb=2'),
    ]
    for seq, text in lines:
        append(store, seq, text)

    # Default literal: case-insensitive, substring
    assert [r['seq'] for r in store.reader('j', 'a', query='run')['rows']] == [1, 2, 3]
    # Literal + case_sensitive
    assert [r['seq'] for r in store.reader('j', 'a', query='Run', case_sensitive=True)['rows']] == [2]
    assert [r['seq'] for r in store.reader('j', 'a', query='RUN', case_sensitive=True)['rows']] == [1, 3]
    # Literal + whole_word (case-insensitive)
    assert [r['seq'] for r in store.reader('j', 'a', query='run', whole_word=True)['rows']] == [1, 2]
    # Literal + whole_word + case_sensitive
    assert [r['seq'] for r in store.reader('j', 'a', query='run', case_sensitive=True, whole_word=True)['rows']] == [1]
    assert [r['seq'] for r in store.reader('j', 'a', query='Run', case_sensitive=True, whole_word=True)['rows']] == [2]
    # Literal + whole_word escapes regex metacharacters
    assert [r['seq'] for r in store.reader('j', 'a', query='a.b', whole_word=True)['rows']] == [5]
    assert [r['seq'] for r in store.reader('j', 'a', query='aXb', whole_word=True)['rows']] == [5]

    # Regex: default case-insensitive vs case_sensitive
    assert [r['seq'] for r in store.reader('j', 'a', query=r'run|err', regex=True)['rows']] == [1, 2, 3, 4]
    assert [r['seq'] for r in store.reader('j', 'a', query=r'Run|ERR', regex=True, case_sensitive=True)['rows']] == [2, 4]
    # Regex + whole_word wraps alternation in non-capturing group \b(?:...)\b
    assert [r['seq'] for r in store.reader('j', 'a', query=r'run|err', regex=True, whole_word=True)['rows']] == [1, 2, 4]
    assert [r['seq'] for r in store.reader('j', 'a', query=r'Run|err', regex=True, case_sensitive=True, whole_word=True)['rows']] == [2, 4]
    assert [r['seq'] for r in store.reader('j', 'a', query=r'RUNNER| ERRORS', regex=True, whole_word=True)['rows']] == [1, 4]

    # Invalid regex rejected even when whole_word=True wraps in (?:...)
    with pytest.raises(ValueError, match='Invalid regular expression'):
        store.reader('j', 'a', query='a)(b', regex=True, whole_word=True)

    # Type validation for case_sensitive and whole_word
    for bad in ('true', 1, None):
        with pytest.raises(ValueError, match='case_sensitive'):
            store.reader('j', 'a', query='run', case_sensitive=bad)
        with pytest.raises(ValueError, match='whole_word'):
            store.reader('j', 'a', query='run', whole_word=bad)

    # Cursor scope invalidates when case_sensitive or whole_word toggles
    base_page = store.reader('j', 'a', query='run', limit=1)
    assert base_page['next']
    with pytest.raises(ValueError, match='Cursor'):
        store.reader('j', 'a', query='run', case_sensitive=True, cursor=base_page['next'])
    with pytest.raises(ValueError, match='Cursor'):
        store.reader('j', 'a', query='run', whole_word=True, cursor=base_page['next'])
