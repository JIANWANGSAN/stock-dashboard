"""AStock integration contracts; synthetic inputs, no paid provider/network calls."""
import json
from types import SimpleNamespace
from pathlib import Path
import pytest
from pydantic import ValidationError
from review_agent.backtesting import BacktestSpec, calculate
from review_agent.api import PageChatInput
from test_page_chat_alignment import manager, submit

SPEC = dict(codes=['600519.SH'],start='2023-01-01',end='2025-12-31',strategy='ma_cross',params={'fast':20,'slow':60},initial_cash=100000)

@pytest.mark.parametrize('patch', [dict(codes=['../private']),dict(codes=['AAPL','AAPL']),dict(end='2099-01-01'),dict(start='2025-02-31'),dict(params={'url':'https://evil.test'}),dict(initial_cash=float('nan')),dict(allow_short='false'),dict(style='intraday')])
def test_invalid_spec_rejected(patch):
    with pytest.raises((ValueError,ValidationError)): BacktestSpec.model_validate(SPEC|patch)


def body(rid='c'*32,spec=None):
    return PageChatInput(messages=[{'role':'user','content':'验证均线交叉'}],task_kind='backtest',backtest_args=spec,
                         request_id=rid,llm={'provider':'claude','model':'default'})


def test_clarify_is_not_report_and_does_not_execute(tmp_path,monkeypatch):
    m=manager(tmp_path);calls=[]
    m.runtime._invoke=lambda *a,**k:calls.append(a[1]) or json.dumps({'answer':'请确认条件','spec':SPEC})
    monkeypatch.setattr('review_agent.public_worker.fetch_public',lambda *a,**k:pytest.fail('not yet confirmed'))
    try:
        submit(m,body());submit(m,body())
        row=m.page_chats.status('c'*32)
        assert row['status']=='complete' and row['backtest_spec']['codes']==['600519.SH']
        assert 'backtest_result' not in row and len(calls)==1 and calls[0]['provider']=='claude'
    finally:m.shutdown()


def test_confirm_executes_once_and_archive_survives_restart(tmp_path,monkeypatch):
    m=manager(tmp_path);calls=[]
    m.runtime._invoke=lambda *a,**k:pytest.fail('confirmed params must not bill model')
    def fetch(name,args,directory,check,**kw):
        calls.append((name,args));return {'ok':True,'result':{'metrics':{'total_return':.125},'provenance':[{'code':'600519.SH','rows':500}]}}
    monkeypatch.setattr('review_agent.public_worker.fetch_public',fetch)
    try:
        submit(m,body(spec=SPEC));submit(m,body(spec=SPEC));row=m.page_chats.status('c'*32)
        assert row['backtest_result']['metrics']['total_return']==.125 and len(calls)==1
        assert calls[0][0]=='run_backtest' and 'apiKey' not in json.dumps(row)
    finally:m.shutdown()
    m=manager(tmp_path)
    try: assert m.page_chats.status('c'*32)['backtest_result']==row['backtest_result']
    finally:m.shutdown()


def test_refusal_is_not_success_report(tmp_path,monkeypatch):
    m=manager(tmp_path)
    monkeypatch.setattr('review_agent.public_worker.fetch_public',lambda *a,**kw:{'ok':False,'refused':{'reason':'资料不足','remedy':'调整日期'}})
    try:
        submit(m,body(spec=SPEC));row=m.page_chats.snapshot()
        assert row['backtest_refused'] and 'backtest_result' not in row and '资料不足' in row['answer']
    finally:m.shutdown()


def test_cancelled_request_never_runs(tmp_path,monkeypatch):
    m=manager(tmp_path)
    monkeypatch.setattr('review_agent.public_worker.fetch_public',lambda *a,**kw:pytest.fail('cancelled'))
    try:
        with m.lock:m.page_chats.cancel('c'*32)
        submit(m,body(spec=SPEC));assert m.page_chats.snapshot()['status']!='complete'
    finally:m.shutdown()


def test_fetch_adapter_preserves_adjustment_and_raw(tmp_path,monkeypatch):
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'research_data'))
    import fetch_backtest as f
    from sources._http import record_raw, dump_json_bytes
    def data(symbol,**kwargs):
        assert symbol=='600519.SH' and kwargs['adjustflag']=='2'
        record_raw(dump_json_bytes({'query':{'code':'sh.600519'},'rows':[{'date':'2024-01-02','close':100}]}),'json',kind='extracted')
        return [{'date':'2024-01-02','close':100}]
    monkeypatch.setattr(f,'baostock_kdata',data)
    envelope=f.fetch('bs_kline_qfq','600519.SH',{},str(tmp_path))
    ref=envelope['extra']['raw_files'][0]
    assert json.loads((tmp_path/ref).read_text())['query']['code']=='sh.600519'
    with pytest.raises(ValueError):f.fetch('unknown','600519.SH',{},str(tmp_path))


def test_relative_date_prompt_uses_china_today(tmp_path, monkeypatch):
    m=manager(tmp_path);prompts=[]
    monkeypatch.setattr('review_agent.backtesting.china_today',lambda:'2026-09-08')
    m.runtime._invoke=lambda *a,**k:prompts.append(a[3]) or json.dumps({'answer':'请确认条件','spec':SPEC})
    try:
        submit(m,body());assert '北京时间今天是 2026-09-08' in prompts[0]
        assert m.page_chats.snapshot()['status']=='complete'
        with pytest.raises(ValueError):BacktestSpec.model_validate(SPEC|{'end':'2026-09-08'})
    finally:m.shutdown()


@pytest.mark.parametrize('answer',['长'*4001,'建议买入这个标的'])
def test_invalid_condition_answer_not_published(tmp_path,answer):
    m=manager(tmp_path)
    m.runtime._invoke=lambda *a,**k:json.dumps({'answer':answer,'spec':SPEC},ensure_ascii=False)
    try:
        submit(m,body());row=m.page_chats.snapshot()
        assert row['status']=='failed' and not row.get('answer') and not row.get('backtest_result')
    finally:m.shutdown()


def test_data_failure_retains_confirmed_spec(tmp_path,monkeypatch):
    from review_agent.evidence import EvidenceError
    m=manager(tmp_path)
    def fetch(*a,**k):raise EvidenceError('源站超时')
    monkeypatch.setattr('review_agent.public_worker.fetch_public',fetch)
    try:
        submit(m,body(spec=SPEC));row=m.page_chats.snapshot()
        assert row['status']=='failed' and row['backtest_spec']['codes']==SPEC['codes']
        assert not row.get('backtest_result')
    finally:m.shutdown()


def test_probability_invalid_snapshot_does_not_replace_cache(tmp_path,monkeypatch):
    from review_agent import probability_cache as p
    from review_agent.evidence import EvidenceError
    monkeypatch.setattr(p,'_value',None);monkeypatch.setattr(p,'_updated',0)
    clock=[1000.0];monkeypatch.setattr(p.time,'monotonic',lambda:clock[0])
    good=dict(items=[],as_of='2026-09-08T00:00:00Z',guard='有限采样',warnings=[],errors=[],sources_partial=[])
    results=iter([{},good,{'items':None}])
    monkeypatch.setattr(p,'fetch_public',lambda *a,**k:next(results))
    with pytest.raises(EvidenceError):p.get_probability(tmp_path)
    first=p.get_probability(tmp_path);assert not first['cached']
    clock[0]+=30;cached=p.get_probability(tmp_path)
    assert cached['cached'] and cached['cache_age_seconds']==30
    clock[0]+=300;stale=p.get_probability(tmp_path)
    assert stale['stale'] and stale['items']==[] and stale['refresh_error']


def test_probability_rejects_invalid_item():
    from review_agent.probability_cache import ProbabilitySnapshot
    with pytest.raises(ValidationError):
        ProbabilitySnapshot.model_validate(dict(items=[{'prob':2}],as_of='now',guard='',warnings=[],errors=[],sources_partial=[]))


def test_hk_adapter_normalizes_case(tmp_path,monkeypatch):
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'research_data'))
    import fetch_backtest as f
    from sources._http import record_raw
    def data(symbol,**args):
        assert symbol=='0700' and args['market']=='HK'
        record_raw(b'{}','json',kind='extracted');return [{}]
    monkeypatch.setattr(f,'yahoo_kline',data)
    assert f.fetch('yahoo_kline','0700.hk',{},str(tmp_path))['status']=='ok'


def test_probability_accepts_json_integer_zero_as_real_zero():
    from review_agent.probability_cache import ProbabilityItem
    item=ProbabilityItem.model_validate(dict(module='宏观',venue='kalshi',title='测试合约',leg='yes',prob=0,volume=0,volume_missing=False,close='',as_of='2026-09-08T00:00:00Z',ticker='X'))
    assert item.prob==0.0 and item.volume==0.0 and item.volume_missing is False
