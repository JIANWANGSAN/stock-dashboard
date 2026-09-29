"""Explicit web import boundaries; no real user files or models."""
import json
from starlette.requests import Request


def request():
    return Request({'type':'http','method':'POST','path':'/api/positions/import-legacy','headers':[(b'host',b'127.0.0.1'),(b'origin',b'http://127.0.0.1')],'server':('127.0.0.1',80),'scheme':'http'})


def test_selected_import_validates_before_any_write(monkeypatch):
    import server
    from duanxian import journal
    calls=[];monkeypatch.setattr(journal,'add_trade',lambda **kw:calls.append(kw))
    result=server.api_positions_import(request(),{'holdings':[{'code':'600519','shares':100,'cost':100},None]})
    assert result.status_code==400 and calls==[]


def test_selected_import_deduplicates_within_file(monkeypatch):
    import server
    from duanxian import journal,positions
    calls=[];monkeypatch.setattr(journal,'add_trade',lambda **kw:calls.append(kw))
    monkeypatch.setattr(positions,'open_positions',lambda:[])
    row={'code':'600519','shares':100,'cost':100,'name':'合成测试','date':'2026-09-07'}
    result=server.api_positions_import(request(),{'holdings':[row,row]})
    assert json.loads(result.body)['imported']==1 and json.loads(result.body)['skipped']==1
    assert len(calls)==1


def test_import_dates_prevalidated(monkeypatch):
    import server
    from duanxian import journal
    writes=[];monkeypatch.setattr(journal,'add_trade',lambda **kw:writes.append(kw))
    good={'code':'600519','shares':100,'cost':10,'date':'2026-09-01'}
    response=server.api_positions_import(request(),{'holdings':[good,{**good,'date':'bad-date'}]})
    assert response.status_code==400 and writes==[]


def test_import_identity_survives_aggregation(tmp_path,monkeypatch):
    import server
    from duanxian import journal
    # Raw store is in memory; all context and fee reads are replaced with fixtures.
    rows=[]
    monkeypatch.setattr(journal,'_load_raw',lambda:list(rows))
    def save(items):rows[:]=items;return True
    monkeypatch.setattr(journal,'_save',save)
    monkeypatch.setattr(journal,'_market_context',lambda day:{})
    monkeypatch.setattr(journal,'_stock_context',lambda day,code:{})
    monkeypatch.setattr(journal,'load_fees',lambda:journal.DEFAULT_FEES.copy())
    a={'code':'600519','shares':100,'cost':10,'date':'2026-09-01'}
    b={**a,'shares':200,'cost':20}
    first=json.loads(server.api_positions_import(request(),{'holdings':[a,b]}).body)
    second=json.loads(server.api_positions_import(request(),{'holdings':[a,b]}).body)
    assert first['imported']==2 and second['imported']==0 and second['skipped']==2
    assert len(rows)==2 and all(r.get('legacy_import_id') for r in rows)
