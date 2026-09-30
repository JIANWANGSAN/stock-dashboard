import json
from pathlib import Path
import pytest
from review_agent.evidence import ToolSession, EvidenceError
from review_agent.public_data import normalize, query_args, source_url, stock_symbol


def receipt(args, rows):
    return {'args':args,'fetched_at':'2026-09-05T01:00:00+00:00','raw':json.dumps({'code':0,'data':{stock_symbol(args['symbol']):{'day':rows}}})}


def bundle():
    return {'anchor':'2026-09-02','dates':[],'scope':'test','evidence':[],'gaps':[], 'context':{'allow_network':True,'symbol':'300308'}}


def test_source_dates_symbols_and_raw_prices():
    args=query_args('300308','2026-09-01','2026-09-02','2026-09-02')
    rows=[['2026-09-01','10','12','13','9'],['2026-09-02','12','11','12','10'],['2026-09-03','12','15','16','10']]
    records,gaps=normalize(receipt(args,rows),args)
    assert [r['value'] for r in records]==[12,11]
    assert all(r['date']<='2026-09-02' and r['source_sha256'] and r['fetched_at'] for r in records)
    assert 'qfq' not in source_url(args) and 'hfq' not in source_url(args)
    for symbol in ['../x','sh600519','510300','000001?x=secret']:
        with pytest.raises(EvidenceError):stock_symbol(symbol)
    with pytest.raises(EvidenceError):query_args('300308','2026-09-01','2026-09-03','2026-09-02')
    assert normalize(receipt(args,[['2026-09-01','10','NaN','13','9']]),args)[0]==[]
    assert normalize(receipt(args,[rows[0],rows[0]]),args)[0]==[]


def test_controlled_network_replays_without_network_and_tracks_failures(tmp_path, monkeypatch):
    import review_agent.public_data as source
    calls=[]
    def fetch(args):
        calls.append(args)
        return receipt(args,[['2026-09-01','10','12','13','9'],['2026-09-02','12','11','12','10']])
    monkeypatch.setattr(source,'retrieve',fetch)
    log=tmp_path/'tools.jsonl'; session=ToolSession(bundle(),log)
    with pytest.raises(EvidenceError):session.fetch_stock_prices('600519','2026-09-01','2026-09-02')
    result=session.fetch_stock_prices('300308','2026-09-01','2026-09-02')
    first,last=result['evidence'];calc=session.compare_stock_prices(first['id'],last['id'])
    assert calc['value']==-1
    assert session.submit_answer('incomplete',[{'text':'收盘读数下降。','citations':[calc['id']]}],['尚未覆盖基本面'])['accepted']
    replay=ToolSession(bundle(),log);replay.replay()
    assert len(calls)==1 and replay.answer==session.answer
    with pytest.raises(EvidenceError):session.fetch_stock_prices('300308','2026-09-01','2026-09-02')
    disabled=bundle();disabled['context']['allow_network']=False
    with pytest.raises(EvidenceError,match='尚未启用'):ToolSession(disabled,tmp_path/'disabled.jsonl').fetch_stock_prices('300308','2026-09-01','2026-09-02')
    assert len(calls)==1


def test_failed_source_is_a_persistent_gap(tmp_path, monkeypatch):
    import review_agent.public_data as source
    monkeypatch.setattr(source,'retrieve',lambda args:{'args':args,'fetched_at':'2026-09-05T01:00:00+00:00','error':'timeout'})
    session=ToolSession(bundle(),tmp_path/'tools.jsonl')
    assert session.fetch_stock_prices('300308','2026-09-01','2026-09-02')['status']=='unavailable'
    session.submit_answer('incomplete',[{'text':'未能获取行情。','citations':[]}],['来源不可用'])
    replay=ToolSession(bundle(),session.log);replay.replay()
    assert any('获取失败' in g for g in replay.answer['gaps'])


def test_price_followup_retains_dates_and_symbol_without_old_fact_values():
    from review_agent.runtime import conversation_history
    turn={'status':'incomplete','question':'比较近期走势','result':{'gaps':['缺少基本面'],'findings':[],
          'evidence':[{'id':'SECRET_OLD_ID','kind':'price_change','metric':'stock_close','symbol':'300308',
                       'first_date':'2026-07-20','date':'2026-07-29','value':123456789}]}}
    history=conversation_history([turn])
    coordinates=history[0]['comparisons_to_recompute_if_referenced'][0]
    assert coordinates=={'metric':'stock_close','symbol':'300308','first_date':'2026-07-20','last_date':'2026-07-29'}
    assert 'SECRET_OLD_ID' not in json.dumps(history) and '123456789' not in json.dumps(history)


def test_public_cache_write_failure_and_corruption_are_evidence_errors(tmp_path,monkeypatch):
    import review_agent.public_data as source
    import duanxian.util as util
    from review_agent.evidence import digest
    args=query_args('300308','2026-09-01','2026-09-02','2026-09-02')
    monkeypatch.setattr(source,'retrieve',lambda args:receipt(args,[['2026-09-01','10','12','13','9']]))
    monkeypatch.setattr(util,'atomic_write_json',lambda *a:False)
    with pytest.raises(EvidenceError,match='写入失败'):
        ToolSession(bundle(),tmp_path/'write.jsonl').fetch_stock_prices(**args)
    cache=tmp_path/('public-'+digest(args)[:16]+'.json');cache.write_text('{broken')
    with pytest.raises(EvidenceError,match='缓存损坏'):
        ToolSession(bundle(),tmp_path/'corrupt.jsonl').fetch_stock_prices(**args)
    assert cache.read_text()=='{broken'


def test_public_cache_cannot_exceed_its_replay_read_limit(tmp_path,monkeypatch):
    import review_agent.public_data as source
    monkeypatch.setattr(source,'retrieve',lambda args:{'raw':'x'*2_000_001})
    with pytest.raises(EvidenceError,match='超过读取上限'):
        ToolSession(bundle(),tmp_path/'large.jsonl').fetch_stock_prices('300308','2026-09-01','2026-09-02')
    assert not list(tmp_path.glob('public-*'))
