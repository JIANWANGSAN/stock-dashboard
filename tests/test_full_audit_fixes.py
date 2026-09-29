"""User audit regressions. Only temporary state and deterministic data."""
from datetime import datetime
import json
import pytest

@pytest.mark.parametrize('clock,slot',[('23:06',None),('12:00',None),('09:27',None),('15:09','15:00'),('11:31','11:29'),('14:00','../14:00')])
def test_snapshot_rejects_non_trading_windows_before_fetch(monkeypatch,clock,slot):
 from duanxian import intraday as i
 monkeypatch.setattr(i,'china_today',lambda:'2026-09-08')
 monkeypatch.setattr(i,'china_now',lambda:datetime.fromisoformat('2026-09-08T'+clock))
 monkeypatch.setattr(i.trade_calendar,'quote_trade_day',lambda:'2026-09-08')
 monkeypatch.setattr(i.trade_calendar,'prev_trade_date',lambda d:pytest.fail('invalid window reached data fetching'))
 assert i.capture(slot)['ok'] is False

@pytest.mark.parametrize('slot,clock',[('09:25','09:29'),('09:30','09:31'),('11:30','11:35'),('13:00','13:03'),('15:00','15:08')])
def test_snapshot_valid_windows(slot,clock):
 from duanxian.intraday import _valid_session_window
 assert _valid_session_window(slot,clock)

def test_night_snapshot_quarantined_without_deleting_original(tmp_path,monkeypatch):
 from duanxian import intraday as i
 monkeypatch.setattr(i,'_DIR',str(tmp_path));day=tmp_path/'2026-09-08';day.mkdir()
 for slot in ['23:06','12:00','10:00']:
  (day/(slot.replace(':','')+'.json')).write_text(json.dumps(dict(schema=3,date='2026-09-08',slot=slot,captured_at='2026-09-08 '+slot+':00 CST')))
 result=i.load_day('2026-09-08')
 assert [s['slot'] for s in result['slots']]==['10:00']
 assert len(result['warnings'])==2 and len(list(day.glob('*.json')))==3

def test_align_microseconds_matches_nanoseconds_without_mutation():
 import pandas as pd
 from backtest.engines.base import _align
 idx=pd.date_range('2026-01-01',periods=4).as_unit('us')
 frame=pd.DataFrame({'close':[10,11,10,12]},index=idx);sig=pd.Series([1,1,0,0],index=idx)
 a=_align({'600000.SH':frame},{'600000.SH':sig},['600000.SH'])
 b=_align({'600000.SH':frame.set_axis(idx.as_unit('ns'))},{'600000.SH':sig.set_axis(idx.as_unit('ns'))},['600000.SH'])
 assert a[0].equals(b[0]) and a[0][0].year==2026
 for x,y in zip(a[1:],b[1:]):pd.testing.assert_frame_equal(x,y)
 assert frame.index.unit=='us' and sig.index.unit=='us'

def test_missing_optimizer_fails_instead_of_substituting_weights():
 from backtest.engines.base import _load_optimizer
 with pytest.raises(ValueError,match='未替换策略'):_load_optimizer({'optimizer':'not_installed'})

def test_store_delete_with_followup_preserves_other_conversation(tmp_path):
 from review_agent.store import Store
 from review_agent.followup import Followup
 from review_agent.evidence import EvidenceError
 import time
 store=Store(tmp_path);Followup(store,tmp_path/'reviews')
 with store.connect() as db:
  for c,t,o in [('a'*32,'b'*32,'c'*32),('d'*32,'e'*32,'f'*32)]:
   db.execute('INSERT INTO conversations VALUES (?,?,?,?,?,?)',(c,'2026-09-08','test','{}','{}',time.time()))
   db.execute("INSERT INTO turns (id,conversation_id,request_id,fingerprint,question,status,created,updated) VALUES (?,?,?,?,?,'complete',?,?)",(t,c,t,'test','test',time.time(),time.time()))
   db.execute('INSERT INTO observations (id,turn_id,metric,direction,baseline,created) VALUES (?,?,?,?,?,?)',(o,t,'limit_up_count','上升','{}',time.time()))
   db.execute('INSERT INTO observation_checks VALUES (?,?,?,?)',(t,o,'{}',time.time()))
 assert store.delete_conversation('a'*32)=={'ok':True}
 with store.connect() as db:
  for table in ('conversations','turns','observations','observation_checks'):
   assert db.execute('SELECT count(*) FROM '+table).fetchone()[0]==1
  db.execute("UPDATE turns SET status='running'")
 with pytest.raises(EvidenceError,match='运行'): store.delete_conversation('d'*32)

def test_cache_revision_preserved_and_recent_data_expires(tmp_path,monkeypatch):
 from duanxian import cache_policy as c
 import os,time
 p=tmp_path/'2026-09-08.json'
 assert c.write(str(p),{'count':73})
 assert c.write(str(p),{'count':74})
 assert json.loads(p.read_text())=={'count':74}
 revisions=list((tmp_path/'_revisions'/'2026-09-08').glob('*.json'))
 assert len(revisions)==1 and json.loads(revisions[0].read_text())=={'count':73}
 os.utime(p,(0,0))
 # near history must be revisited, rather than frozen forever
 assert not c.fresh(str(p),'2026-09-08')

def test_post_review_partial_failure_does_not_skip_other_captures(monkeypatch):
 from review_agent import post_review as p
 seen=[]
 def broken(d):raise OSError('synthetic private path must not leak')
 monkeypatch.setattr(p.theme_tree,'capture',broken)
 monkeypatch.setattr(p.backtest,'capture',lambda d:seen.append('corpus') or {'ok':True})
 monkeypatch.setattr(p.archive,'capture_day',lambda d:seen.append('archive') or {'ok':True})
 monkeypatch.setattr(p.reflection,'auto_evaluate_prior',lambda d, **kw:None)
 out=p.capture_after_review('2026-09-08')
 assert not out['theme_reasons']['ok'] and seen==['corpus','archive']
 assert 'private path' not in str(out) and out['reflection']['status']=='not_applicable'

def test_pool_snapshot_shared_but_not_mutable(monkeypatch):
 from duanxian.pool_source import frame
 import akshare as ak,pandas as pd
 calls=[]
 monkeypatch.setattr(ak,'stock_zt_pool_em',lambda **kw: calls.append(kw) or pd.DataFrame({'代码':['600000']}))
 a=frame('zt','2026-09-08');a.loc[0,'代码']='corrupted'
 assert frame('zt','20260908').iloc[0]['代码']=='600000' and len(calls)==1

def test_all_api_auth_and_both_origin_headers(monkeypatch):
 from fastapi.testclient import TestClient
 import server
 monkeypatch.setattr(server,'_VR_API_KEY','audit-synthetic')
 c=TestClient(server.app,base_url='http://127.0.0.1')
 for path in ('/api/review/dates','/api/journal/list','/api/verification/menu'):
  assert c.get(path).status_code==401
  assert c.get(path,headers={'Authorization':'Bearer wrong'}).status_code==401
  assert c.get(path,headers={'Authorization':'Bearer audit-synthetic'}).status_code==200
  assert c.get(path,headers={'Authorization':'Bearer audit-synthetic','Origin':'http://127.0.0.1','Referer':'http://foreign.invalid/'}).status_code==403
 assert c.get('/api/backtest?refresh=1',headers={'Authorization':'Bearer audit-synthetic'}).status_code==405

def test_monitor_clients_union_and_get_does_not_mutate(monkeypatch):
 from vr import watchtower as w
 monkeypatch.setattr(w,'_watch_clients',{})
 monkeypatch.setattr(w,'_extra_watch',set())
 monkeypatch.setattr(w,'poke',lambda:None)
 w.set_client_watch('a'*32,['600000']);w.set_client_watch('b'*32,['000001'])
 assert set(w._extra_watch)=={'600000','000001'}
 w.set_client_watch('a'*32,[])
 assert set(w._extra_watch)=={'000001'}

@pytest.mark.parametrize('case',['t1','limit_down','halt'])
def test_terminal_close_obeys_execution_rules(case):
 import pandas as pd
 from backtest.tests.test_engine_rules import frame,run_engine,ChinaAEngine
 df=frame([100.,100.,100.,100.,90.] if case=='limit_down' else [100.]*5)
 if case=='halt':df.loc[df.index[-1],'volume']=0
 signals=pd.Series([0,0,0,.5,.5] if case=='t1' else [.5]*5,index=df.index)
 eng,result=run_engine(ChinaAEngine,{'600519.SH':df},{'600519.SH':signals},adjustment='hold')
 assert result['unclosed_positions']==1 and len(eng.positions)==1
 assert len(eng.trades)==0
 assert eng.equity_snapshots[-1].equity>eng.capital

def test_yahoo_outside_window_invalid_bar_not_counted():
 from backtest.loader import _frame_from_yahoo
 from datetime import timezone
 stamp=lambda d:int(datetime.fromisoformat(d).replace(tzinfo=timezone.utc).timestamp())
 payload={'chart':{'result':[{'meta':{'exchangeTimezoneName':'UTC'},'timestamp':[stamp('2025-01-01'),stamp('2026-01-02')], 'indicators':{'quote':[{'open':[None,10],'high':[None,11],'low':[None,9],'close':[None,10],'volume':[None,100]}]}}]}}
 frame,dropped=_frame_from_yahoo(payload,'2026-01-01','2026-01-03')
 assert len(frame)==1 and dropped==0


def test_pool_slow_source_does_not_block_other_kind(monkeypatch):
 import threading
 import akshare as ak
 import pandas as pd
 from duanxian.pool_source import frame
 entered=threading.Event();release=threading.Event()
 def slow(**kw):
  entered.set();release.wait(2);return pd.DataFrame({'代码':['600000']})
 monkeypatch.setattr(ak,'stock_zt_pool_em',slow)
 monkeypatch.setattr(ak,'stock_zt_pool_dtgc_em',lambda **kw:pd.DataFrame({'代码':['600001']}))
 thread=threading.Thread(target=lambda:frame('zt','2026-09-09'));thread.start()
 try:
  assert entered.wait(1)
  assert frame('dt','2026-09-09').iloc[0]['代码']=='600001'
  assert thread.is_alive()
 finally:release.set();thread.join(2)


def test_watch_heartbeat_does_not_wake_unchanged_pool_and_expires(monkeypatch):
 from vr import watchtower as w
 calls=[];now=[10.]
 monkeypatch.setattr(w,'_watch_clients',{});monkeypatch.setattr(w,'_extra_watch',[])
 monkeypatch.setattr(w,'poke',lambda:calls.append('wake'))
 monkeypatch.setattr(w.time,'monotonic',lambda:now[0])
 w.set_client_watch('1'*32,['600000']);w.set_client_watch('1'*32,['600000'])
 assert calls==['wake']
 now[0]=131.;w._expire_watch_clients()
 assert not w._extra_watch and not w._watch_clients


def test_align_ignores_unrequested_bad_index_and_explains_requested_one():
 import pandas as pd
 from backtest.engines.base import _align
 idx=pd.date_range('2026-01-01',periods=3)
 df=pd.DataFrame({'close':[1,2,3]},index=idx);sig=pd.Series([1,1,1],index=idx)
 dates,*_=_align({'a':df,'extra':pd.DataFrame({'close':[1]})},{'a':sig},['a'])
 assert dates[0].year==2026
 with pytest.raises(ValueError,match='DatetimeIndex'):_align({'a':df},{'a':pd.Series([1,1,1])},['a'])


def test_reflection_failure_is_not_success(monkeypatch):
 from review_agent import post_review as p
 for module,name in [(p.theme_tree,'capture'),(p.backtest,'capture'),(p.archive,'capture_day')]:
  monkeypatch.setattr(module,name,lambda d:{'ok':True})
 def failed(*a,**kw):raise RuntimeError('private synthetic detail')
 monkeypatch.setattr(p.reflection,'auto_evaluate_prior',failed)
 result=p.capture_after_review('2026-09-08')
 assert not result['reflection']['ok'] and 'private' not in str(result)


def test_capture_timeout_kills_worker_and_releases_lock(monkeypatch):
 from review_agent import post_review as p
 import subprocess,os
 kills=[]
 class Worker:
  pid=123456;returncode=None
  def __init__(self,*a,**kw):pass
  def poll(self):return None
  def wait(self,timeout):return 0
  def kill(self):kills.append('kill')
 monkeypatch.setattr(subprocess,'Popen',Worker)
 monkeypatch.setattr(os,'killpg',lambda *a:kills.append('kill'))
 for _ in range(2):
  result=p.capture_bounded('2026-09-08',timeout=0)
  assert not result['capture']['ok'] and '超时' in result['capture']['reason']
 assert len(kills)==2


def test_buy_hold_effective_plan_and_two_symbol_trades(tmp_path,monkeypatch):
 import importlib
 import pandas as pd
 from backtest.gate import plan_backtest
 from backtest.strategies import BuyAndHold
 module=importlib.import_module('backtest.run')
 idx=pd.bdate_range('2024-01-01',periods=260)
 class Loader:
  failures={};provenance={}
  def __init__(self,*a,**kw):pass
  def fetch(self,codes,start_date,end_date,interval='1D',**kw):
   return {c:pd.DataFrame({'open':[10+i*(.01 if n else .02) for i in range(260)],'high':[20]*260,'low':[9]*260,'close':[10+i*(.01 if n else .02) for i in range(260)],'volume':[1000000]*260},index=idx) for n,c in enumerate(codes)}
 monkeypatch.setattr(module,'VibeLoader',Loader)
 plan=plan_backtest(codes=['600000.SH','600001.SH'],start='2024-01-01',end='2025-01-01',style='swing')
 result=module.run(plan,BuyAndHold(),tmp_path)
 assert result.plan.to_config()['position_adjustment']=='hold'
 assert plan.to_config()['position_adjustment']=='rebalance', 'caller-owned plan preserved'
 assert any('不调仓' in n for n in result.notes)
 assert result.metrics['trade_count']==2
 card=json.loads((tmp_path/'run_card.json').read_text())
 assert card['backtest']['position_adjustment']=='hold'

@pytest.mark.parametrize('latest_target',['unresolvable','missing_data'])
def test_reflection_bad_latest_does_not_starve_earlier_candidate(monkeypatch,tmp_path,latest_target):
 from duanxian import reflection as r,review_store
 monkeypatch.setattr(r,'_REFLECT_DIR',str(tmp_path))
 monkeypatch.setattr(review_store,'dates',lambda:['2026-09-07','2026-09-04'])
 monkeypatch.setattr(r,'_next_trade_date',lambda d:'2026-09-08')
 monkeypatch.setattr(r,'_today',lambda:'2026-09-09')
 monkeypatch.setattr(r,'_load_review',lambda d:{'focus':{'focus_directions':[{'leader_candidates':['unknown']}], 'emotion_phase':'unknown'}})
 monkeypatch.setattr(r,'_name_code_map',lambda:{})
 monkeypatch.setattr(r,'_resolve_code',lambda *a:None if latest_target=='unresolvable' else '600000')
 visited=[]
 def evaluate(day,**kw):
  visited.append(day)
  return None if day=='2026-09-07' else {'evaluated':day}
 monkeypatch.setattr(r,'evaluate',evaluate)
 assert r.auto_evaluate_prior('2026-09-08',strict=True)=={'evaluated':'2026-09-04'}
 assert visited==['2026-09-07','2026-09-04']


def test_watch_aggregate_cap_rejection_preserves_existing_union(monkeypatch):
 from vr import watchtower as w
 monkeypatch.setattr(w,'_watch_clients',{});monkeypatch.setattr(w,'_extra_watch',[])
 monkeypatch.setattr(w,'poke',lambda:None)
 for i in range(3):w.set_client_watch(str(i)*32,[f'{j:06}' for j in range(i*100,(i+1)*100)])
 before=list(w._extra_watch)
 with pytest.raises(ValueError,match='300'):w.set_client_watch('f'*32,['600000'])
 assert w._extra_watch==before and 'f'*32 not in w._watch_clients

@pytest.mark.parametrize('date_to,expected,stale',[('2026-07-27','2026-09-08',True),('2026-09-08','2026-09-08',False),(None,'2026-09-08',True),('2026-09-08',None,False)])
def test_archive_freshness_never_claims_unseen_period_stable(date_to,expected,stale):
 from duanxian.archive import coverage_status
 result=coverage_status(date_to,expected)
 assert result['stale']==stale
 assert result['last_archived_session']==date_to and result['expected_session']==expected
 if stale:assert '不能' in result['coverage_note']
 elif expected:assert '不代表中间' in result['coverage_note']
 else:assert '未核实' in result['coverage_note']

def test_live_emotion_does_not_relabel_yesterday_as_today(monkeypatch):
    from duanxian import live_emotion as module
    import datetime
    monkeypatch.setattr(module,'china_now',lambda:datetime.datetime(2026,9,9,8,20))
    monkeypatch.setattr(module.trade_calendar,'quote_trade_day',lambda:'2026-09-08')
    monkeypatch.setattr(module,'_pool',lambda *a: (_ for _ in ()).throw(AssertionError('must not read stale pool')))
    value=module.snapshot()
    assert value['available'] is False
    assert '不计算今日晋级率' in value['reason']
    assert 'promotion_rate' not in value


def test_live_pool_failure_stays_missing(monkeypatch):
    from duanxian import live_emotion as module
    from types import SimpleNamespace
    monkeypatch.setitem(module.sys.modules,'astock',SimpleNamespace(_pool_unpinned=lambda *a:None))
    assert module._pool('getTopicDTPool','20260909') is None
    monkeypatch.setitem(module.sys.modules,'astock',SimpleNamespace(_pool_unpinned=lambda *a:[]))
    assert module._pool('getTopicDTPool','20260909') == []

@pytest.mark.parametrize('hour,minute',[(10,30),(15,10)])
def test_live_emotion_keeps_today_and_settled_available(monkeypatch,hour,minute):
    from duanxian import live_emotion as module
    import datetime
    monkeypatch.setattr(module,'_cache',{})
    monkeypatch.setattr(module,'china_now',lambda:datetime.datetime(2026,9,9,hour,minute))
    monkeypatch.setattr(module.trade_calendar,'quote_trade_day',lambda:'2026-09-09')
    monkeypatch.setattr(module.trade_calendar,'is_settled',lambda d:hour>=15)
    monkeypatch.setattr(module.trade_calendar,'prev_trade_date',lambda d:'2026-09-08')
    def pool(kind,day):
        if kind=='getTopicZTPool':
            return [{'c':'600000','lbc':2}] if day=='20260909' else [{'c':'600000'},{'c':'600001'}]
        return []
    monkeypatch.setattr(module,'_pool',pool)
    result=module.snapshot()
    assert result['available'] is True
    assert result['promotion_rate']==.5
    assert result['promotion_base']==2
    assert result['settled']==(hour>=15)
