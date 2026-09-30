"""Yesterday's cohort must retain losers, missing quotes and its actual session date."""
from vr import previous_ladder as ladder


def test_calendar_failure_does_not_guess_weekday(monkeypatch):
    monkeypatch.setattr(ladder.trade_calendar, 'trade_dates_ending_at', lambda *_: [])
    assert ladder.load_cohort('2026-10-08')['available'] is False


def test_holiday_uses_exact_previous_session(monkeypatch):
    monkeypatch.setattr(ladder.trade_calendar, 'trade_dates_ending_at', lambda end, n: ['2026-09-30'])
    seen = []
    def pools(day):
        seen.append(day)
        return {'zt': [{'code':'000001','name':'A','boards':3,'sector':'行业'}]}
    monkeypatch.setattr(ladder.market_facts, 'pools', pools)
    result = ladder.load_cohort('2026-10-08')
    assert seen == ['2026-09-30']
    assert result['sample_date'] == '2026-09-30'
    assert result['quote_date'] == '2026-10-08'
    assert result['stocks'][0]['boards'] == 3


def test_unavailable_pool_is_not_zero_samples(monkeypatch):
    monkeypatch.setattr(ladder.trade_calendar, 'trade_dates_ending_at', lambda *_: ['2026-09-04'])
    monkeypatch.setattr(ladder.market_facts, 'pools', lambda _: None)
    assert not ladder.load_cohort('2026-09-07')['available']


def test_empty_pool_is_distinguished_from_failure(monkeypatch):
    monkeypatch.setattr(ladder.trade_calendar, 'trade_dates_ending_at', lambda *_: ['2026-09-04'])
    monkeypatch.setattr(ladder.market_facts, 'pools', lambda _: {'zt': []})
    result = ladder.load_cohort('2026-09-07')
    assert result['available'] and result['stocks'] == []


def test_missing_and_losing_members_are_not_dropped():
    cohort = {'available':True, 'sample_date':'2026-09-04','quote_date':'2026-09-07', 'stocks':[
        {'code':'000001','name':'winner','boards':3},
        {'code':'000002','name':'loser','boards':4},
        {'code':'000003','name':'missing','boards':2},
        {'code':'000004','name':'stale','boards':1},
    ]}
    quotes = {
        '000001':{'price':11,'pct':10,'zt_price':11,'quote_time':'20260907145900'},
        '000002':{'price':9,'pct':-10,'zt_price':11,'quote_time':'20260907145900'},
        '000004':{'price':11,'pct':10,'zt_price':11,'quote_time':'20260904150000'},
    }
    result = ladder.render_cohort(cohort, quotes)
    assert len(result['stocks']) == 4
    by_code = {r['code']:r for r in result['stocks']}
    assert by_code['000001']['boards'] == 3  # Never silently promote yesterday's tier.
    assert by_code['000001']['status'] == '封板中'
    assert by_code['000002']['status'] == '未封板'
    assert by_code['000002']['pct'] == -10
    for code in ['000003','000004']:
        assert by_code[code]['status'] == '行情待更新'
        assert by_code[code]['pct'] is None
    assert result['covered'] == 2


def test_intraday_touch_is_not_guessed_from_negative_return():
    cohort = {'available':True,'quote_date':'2026-09-07','stocks':[{'code':'000001','name':'A','boards':3}]}
    q={'price':10,'high':11,'zt_price':11,'pct':0,'quote_time':'20260907140000'}
    assert ladder.render_cohort(cohort, {'000001':q})['stocks'][0]['status'] == '触板回落'
    q.pop('high')
    assert ladder.render_cohort(cohort, {'000001':q})['stocks'][0]['status'] == '未封板'


def test_bad_pool_rows_are_disclosed(monkeypatch):
    monkeypatch.setattr(ladder.trade_calendar, 'trade_dates_ending_at', lambda *_: ['2026-09-04'])
    monkeypatch.setattr(ladder.market_facts, 'pools', lambda _: {'zt': [None, {'code':'000001','boards':None}]})
    assert ladder.load_cohort('2026-09-07')['available'] is False


def test_monitor_join_and_poll_pool_include_failed_continuations(monkeypatch):
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "vr"))
    from vr import watchtower as monitor
    cohort = {'available':True,'quote_date':'2026-09-07','sample_date':'2026-09-04','stocks':[
        {'code':'000001','name':'A','boards':1}, {'code':'000002','name':'B','boards':3}]}
    monkeypatch.setattr(monitor, '_market_phase', lambda: 'break')
    monkeypatch.setattr(monitor, '_holdings', lambda: [])
    monkeypatch.setattr(monitor, '_extra_watch', [])
    monkeypatch.setattr(monitor, '_bigcaps', lambda: [])
    monkeypatch.setattr(monitor, '_previous_cohort', lambda: cohort)
    monkeypatch.setattr(monitor, '_turnover_yesterday', lambda: ('昨日', []))
    seen = []
    def quotes(codes):
        seen.extend(codes)
        return {'000002': {'price':9,'pct':-10,'zt_price':11,'quote_time':'20260907140000'}}
    monkeypatch.setattr(monitor, '_tencent_batch', quotes)
    class EndLoop(Exception): pass
    def end(_): raise EndLoop()
    monkeypatch.setattr(monitor._wake, 'wait', end)
    import pytest
    with pytest.raises(EndLoop): monitor._loop()
    assert set(seen) == {'000001','000002'}
    result = monitor.get_snapshot()['yesterday_ladder']
    assert len(result['stocks']) == 2 and result['covered'] == 1
    assert result['stocks'][1]['status'] == '未封板'


def test_monitor_holdings_uses_journal_not_legacy(monkeypatch):
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "vr"))
    from vr import watchtower as monitor
    from duanxian import positions
    expected = [{'code':'000001','shares':100,'cost':10}]
    monkeypatch.setattr(positions, 'open_positions', lambda: expected)
    assert monitor._holdings() == expected


def test_cohort_network_wait_does_not_block_polling_or_spawn_more_workers(monkeypatch):
    import threading
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'vr'))
    from vr import watchtower as monitor
    entered, release, returned = threading.Event(), threading.Event(), threading.Event()
    monkeypatch.setattr(monitor, '_previous_cache', (0.0, '', {}))
    monkeypatch.setattr(monitor, '_previous_worker', None)
    def slow_day():
        entered.set()
        release.wait(3)
        return '2026-09-07'
    monkeypatch.setattr(monitor.previous_ladder.trade_calendar, 'quote_trade_day', slow_day)
    monkeypatch.setattr(monitor.previous_ladder, 'load_cohort', lambda day: {'available': True, 'quote_date': day, 'stocks': []})
    def poll():
        monitor._previous_cohort()
        returned.set()
    caller = threading.Thread(target=poll, daemon=True)
    caller.start()
    try:
        assert entered.wait(1)
        assert returned.wait(0.5), 'quote loop must not wait for upstream calendar'
        worker = monitor._previous_worker
        for _ in range(4):
            assert monitor._previous_cohort()['available'] is False
            assert monitor._previous_worker is worker
    finally:
        release.set()
        caller.join(2)
        if monitor._previous_worker: monitor._previous_worker.join(2)
    assert monitor._previous_cohort()['available'] is True


def test_failed_refresh_retains_only_same_session_sample(monkeypatch):
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'vr'))
    from vr import watchtower as monitor
    old = {'available': True, 'quote_date': '2026-09-07', 'sample_date': '2026-09-04',
           'stocks': [{'code':'000001','boards':3}]}
    monkeypatch.setattr(monitor, '_previous_cache', (0.0, '2026-09-07', old))
    monkeypatch.setattr(monitor.previous_ladder.trade_calendar, 'quote_trade_day', lambda: '2026-09-07')
    monkeypatch.setattr(monitor.previous_ladder, 'load_cohort', lambda day: {'available': False, 'quote_date':day, 'stocks':[]})
    monitor._refresh_previous_cohort('2026-09-07')
    assert monitor._previous_cache[2]['stocks'] == old['stocks']
    assert monitor._previous_cache[2]['warning']
    monkeypatch.setattr(monitor.previous_ladder.trade_calendar, 'quote_trade_day', lambda: '2026-09-08')
    monitor._refresh_previous_cohort('2026-09-08')
    assert monitor._previous_cache[2]['available'] is False
    assert monitor._previous_cache[2]['stocks'] == []
