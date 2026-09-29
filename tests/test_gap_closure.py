import datetime as dt
import json
from types import SimpleNamespace
import pytest
from duanxian import historical_sources as hs, modes


def response(payload=None, text=None):
    return SimpleNamespace(json=lambda:payload, text=text, raise_for_status=lambda:None)


def test_public_reason_without_key_checks_every_date(monkeypatch):
    stamp=dt.datetime(2026,9,7,10,tzinfo=hs.TZ).timestamp()
    row={'code':'600000','first_limit_up_time':stamp,'reason_type':'银行+金融'}
    env={'status_code':0,'data':{'info':[row],'page':{'total':1}}}
    monkeypatch.setattr(hs.dr,'_direct_get',lambda *a,**k:response(env))
    reasons,note=hs.public_limit_reasons('20260907')
    assert reasons=={'600000':'银行+金融'} and '1/1' in note
    row['first_limit_up_time']+=86400
    with pytest.raises(ValueError,match='其他交易日'):hs.public_limit_reasons('20260907')


def test_public_reason_detects_missing_page(monkeypatch):
    monkeypatch.setattr(hs.dr,'_direct_get',lambda *a,**k:response({'status_code':0,'data':{'info':[],'page':{'total':1}}}))
    with pytest.raises(ValueError,match='分页缺失'):hs.public_limit_reasons('20260907')


def test_public_reason_is_used_by_production_missing_key_path(monkeypatch):
    monkeypatch.delenv('IWENCAI_API_KEY',raising=False)
    monkeypatch.setattr(hs,'public_limit_reasons',lambda d:({'600000':'银行'},'已核对'+d))
    assert hs.dr.fetch_zt_reasons('20260907')==({'600000':'银行'},'已核对20260907')


def test_board_history_uses_target_close_and_prior_close(monkeypatch):
    data='20260904,0,0,0,100,0,100000000,,,,0;20260907,0,0,0,105,0,200000000,,,,0;20260908,0,0,0,200,0,300000000,,,,0'
    def get(*a,**k):return response(text='quotebridge_v4_line_bk_881121_01_2026('+json.dumps({'data':data})+')')
    monkeypatch.setattr(hs.dr,'_direct_get',get)
    rows=hs.history('881121','2026-09-07')
    assert len(rows)==2 and rows[-1]['pct']==pytest.approx(5) and rows[-1]['amount']==200000000
    with pytest.raises(ValueError,match='未覆盖'):hs.history('881121','2026-09-06')


@pytest.mark.parametrize('tail',['NaN','-1','Infinity'])
def test_board_history_rejects_invalid_amount(monkeypatch,tail):
    raw={'data':f'20260904,0,0,0,100,0,1;20260907,0,0,0,105,0,{tail}'}
    monkeypatch.setattr(hs.dr,'_direct_get',lambda *a,**k:response(text='quotebridge_v4_line_bk_881121_01_2026('+json.dumps(raw)+')'))
    with pytest.raises(ValueError,match='非法'):hs.history('881121','2026-09-07')


def test_same_day_rule_change_preserves_existing_attribution(tmp_path,monkeypatch):
    from duanxian import util,journal
    monkeypatch.setattr(modes,'_PATH',str(tmp_path/'cards.json'));monkeypatch.setattr(modes,'_DIR',str(tmp_path))
    monkeypatch.setattr(util,'china_today',lambda:'2026-09-08')
    body={'name':'验收','playbook':'打板','setup':'原规则'};modes.save_card(body)
    card=modes.list_cards()['cards'][0]
    modes.save_card({**body,'id':card['id'],'setup':'新规则'})
    monkeypatch.setattr(journal,'list_trades',lambda **k:{'trades':[{'date':'2026-09-08','playbook':'打板','pnl_pct':2},{'date':'2026-09-09','playbook':'打板','pnl_pct':-1}]})
    versions=modes.performance()['cards'][0]['by_version']
    assert [(v['since'],v['trades'],v['avg_pct']) for v in versions]==[('2026-09-08',1,2),('2026-09-09',1,-1)]
    assert not modes.save_card({**body,'id':card['id'],'setup':'新规则'})['new_version']


def test_corrupt_modes_never_overwritten(tmp_path,monkeypatch):
    path=tmp_path/'cards.json';path.write_text('{broken')
    monkeypatch.setattr(modes,'_PATH',str(path))
    with pytest.raises(ValueError,match='原件保留'):modes.save_card({'name':'test','playbook':'打板'})
    assert path.read_text()=='{broken'

@pytest.mark.parametrize('value',[float('inf'),float('nan'),True])
def test_invalid_fee_and_account_values_rejected(value,tmp_path,monkeypatch):
    from duanxian import journal,at_risk
    monkeypatch.setattr(journal,'_FEE_PATH',str(tmp_path/'fees.json'))
    monkeypatch.setattr(at_risk,'_BASE_PATH',str(tmp_path/'base.json'))
    with pytest.raises(ValueError):journal.save_fees({'commission_min':value})
    with pytest.raises(ValueError):at_risk.save_equity_base(value)

@pytest.mark.parametrize('values',[{'max_positions':0.5},{'max_positions':float('inf')},{'max_positions':True},{'max_unplanned_ratio':1.1}])
def test_risk_rule_units_are_enforced(values,tmp_path,monkeypatch):
    from duanxian import risk
    monkeypatch.setattr(risk,'_RULES_PATH',str(tmp_path/'rules.json'))
    with pytest.raises(ValueError):risk.save_rules(values)

def test_relative_trading_day_is_not_a_generated_quantity():
    from review_agent.evidence import has_generated_number
    assert not has_generated_number('相较前一交易日，涨停池扩张。')
    assert has_generated_number('相较前一交易日，涨停增加九家。')

def test_missing_yesterday_pool_does_not_claim_new_theme(monkeypatch):
    from duanxian import theme_tree as tt,market_facts as mf
    row={'code':'600000','name':'甲','boards':1,'first_seal':'093000'}
    monkeypatch.setattr(mf,'pools',lambda d:{'zt':[row],'zb':[],'dt':[]} if d=='2026-09-08' else None)
    monkeypatch.setattr(tt,'reasons_of',lambda d:({'600000':'银行'},None))
    tree=tt.build('2026-09-08','2026-09-07')
    assert tree['themes'][0]['state']!='今日新出现' and '基线不可用' in tree['source_note']


def test_only_generic_reasons_are_not_usable_event_coverage(monkeypatch):
    from duanxian import theme_tree as tt,market_facts as mf
    monkeypatch.setattr(mf,'pools',lambda d:{'zt':[{'code':'600000','name':'甲','boards':1}],'zb':[],'dt':[]})
    monkeypatch.setattr(tt,'reasons_of',lambda d:({'600000':'国企改革'},None))
    assert tt.build('2026-09-08','2026-09-07')['available'] is False


def test_truncated_modes_history_blocks_comparison(tmp_path,monkeypatch):
    from duanxian import util,journal
    monkeypatch.setattr(modes,'_PATH',str(tmp_path/'cards.json'));monkeypatch.setattr(modes,'_DIR',str(tmp_path))
    monkeypatch.setattr(util,'china_today',lambda:'2026-09-08')
    modes.save_card({'name':'验收','playbook':'打板','setup':'v1'})
    card=modes.list_cards()['cards'][0]
    modes.save_card({'id':card['id'],'name':'验收','playbook':'打板','setup':'v2'})
    modes.save_card({'id':card['id'],'name':'验收','playbook':'打板','setup':'v3'})
    rows=[{'date':d,'playbook':'打板','pnl_pct':p} for d,p in [('2026-09-08',2),('2026-09-09',-1)] for _ in range(6)]
    monkeypatch.setattr(journal,'list_trades',lambda **k:{'trades':rows,'total':12})
    result=modes.performance()['cards'][0]
    assert result['latest_vs_prev']['from_version']==1 and result['latest_vs_prev']['to_version']==3
    monkeypatch.setattr(journal,'list_trades',lambda **k:{'trades':rows,'total':13})
    report=modes.performance()
    assert report['truncated'] and report['cards'][0]['latest_vs_prev'] is None


def test_empty_public_reasons_cannot_claim_verified_date(monkeypatch):
    monkeypatch.setattr(hs.dr,'_direct_get',lambda *a,**k:response({'status_code':0,'data':{'info':[],'page':{'total':0}}}))
    with pytest.raises(ValueError,match='无法核对'):hs.public_limit_reasons('20260907')


def test_yesterday_theme_survives_without_today_limit_up(monkeypatch):
    from duanxian import theme_tree as tt,market_facts as mf
    row=lambda code:{'code':code,'name':code,'boards':1}
    monkeypatch.setattr(mf,'pools',lambda d:{'zt':[row('600001')],'zb':[row('600002')],'dt':[]} if d=='2026-09-08' else {'zt':[row('600002')],'zb':[],'dt':[]})
    monkeypatch.setattr(tt,'reasons_of',lambda d:({'600001':'光模块'} if d=='2026-09-08' else {'600002':'机器人'},None))
    themes=tt.build('2026-09-08','2026-09-07',top=1)['themes']
    old=next(t for t in themes if t['tag']=='机器人')
    assert old['limit_up']==0 and old['broken']==1 and old['continuation_rate']==0
    assert old['state']=='昨日题材未再涨停'


def test_partial_yesterday_reasons_cannot_claim_brand_new_theme(monkeypatch):
    from duanxian import theme_tree as tt,market_facts as mf
    row=lambda code:{'code':code,'name':code,'boards':1}
    monkeypatch.setattr(mf,'pools',lambda d:{'zt':[row('600001')],'zb':[],'dt':[]} if d=='2026-09-08' else {'zt':[row('600002'),row('600003')],'zb':[],'dt':[]})
    monkeypatch.setattr(tt,'reasons_of',lambda d:({'600001':'光模块'} if d=='2026-09-08' else {'600002':'机器人'},None))
    result=tt.build('2026-09-08','2026-09-07')
    assert result['prev_covered']==1 and result['prev_total']==2
    assert result['themes'][0]['state']=='昨日覆盖样本未见'


def test_historical_activity_deadline_returns_partial_sample(monkeypatch):
    import time
    import bs4  # 不把首次依赖导入时间算入网络预算
    body=b'<a href="/thshy/detail/code/881001/">A</a><a href="/thshy/detail/code/881002/">B</a>'
    monkeypatch.setattr(hs.dr,'_direct_get',lambda *a,**k:SimpleNamespace(content=body,raise_for_status=lambda:None))
    monkeypatch.setattr(hs,'_HISTORY_BUDGET',.3)
    def history(code,day):
        if code=='881002':time.sleep(.6)
        return [{'date':day,'close':100,'pct':2,'amount':100000000}]
    monkeypatch.setattr(hs,'history',history)
    start=time.monotonic();text=hs.historical_activity('2026-09-07')
    assert time.monotonic()-start<.55
    assert '非完整板块榜单' in text and '覆盖1/2' in text and '超时' in text


def test_deleted_mode_cannot_silently_reappear(tmp_path,monkeypatch):
    monkeypatch.setattr(modes,'_PATH',str(tmp_path/'cards.json'));monkeypatch.setattr(modes,'_DIR',str(tmp_path))
    with pytest.raises(ValueError,match='已被删除'):modes.save_card({'id':'gone','name':'test','playbook':'打板'})
    assert not (tmp_path/'cards.json').exists()


def test_risk_legacy_closed_does_not_stay_open(monkeypatch):
    from duanxian import risk, at_risk
    monkeypatch.setattr(at_risk,'load_equity_base',lambda:None)
    rows=[{'code':str(600000+i),'date':'2026-09-01','pnl_pct':2} for i in range(8)]
    rows += [{'code':'600100','date':'2026-09-08','settled':{'has_fills':True,'first_buy':'2026-09-08','closed':False}}]
    v=risk.violations(rows,dict(risk.DEFAULT_RULES))
    assert not [x for x in v['violations'] if x['rule']=='max_positions']
    assert v['rule_status']['max_positions'].startswith('unavailable')


@pytest.mark.parametrize('bad',[{'code':'','date':'2026-09-01'}, {'code':'600000','date':'2026-9-1'},
    {'code':'600000','date':'2026-09-01','settled':{'first_buy':'2026-09-01','last_sell':'2026-08-31','closed':True}}])
def test_invalid_holding_spans_are_excluded_and_disclosed(bad,monkeypatch):
    from duanxian import risk,at_risk
    monkeypatch.setattr(at_risk,'load_equity_base',lambda:None)
    valid=[{'code':str(600100+i),'date':'2026-09-01'} for i in range(4)]
    rep=risk.violations([*valid,bad],dict(risk.DEFAULT_RULES))
    assert '日期异常' in rep['rule_status']['max_positions']
    hits=[v for v in rep['violations'] if v['rule']=='max_positions']
    assert hits and all(v['actual']==4 for v in hits)


def test_risk_partial_rule_save_preserves_existing_and_corruption(tmp_path,monkeypatch):
    from duanxian import risk,at_risk
    path=tmp_path/'rules.json'
    monkeypatch.setattr(risk,'_RULES_PATH',str(path));monkeypatch.setattr(risk,'_DIR',str(tmp_path))
    risk.save_rules({'max_loss_per_trade_pct':3})
    risk.save_rules({'max_positions':5})
    assert risk.load_rules()['max_loss_per_trade_pct']==3
    path.write_text('{broken')
    with pytest.raises(ValueError,match='损坏'):risk.load_rules()
    with pytest.raises(ValueError,match='损坏'):risk.save_rules({'max_positions':4})
    assert path.read_text()=='{broken'
    risk.save_rules(dict(risk.DEFAULT_RULES))
    assert risk.load_rules()['max_positions']==3
    monkeypatch.setattr(at_risk,'_BASE_PATH',str(path))
    with pytest.raises(ValueError,match='损坏'):at_risk.load_equity_base()


def test_risk_flat_trade_render_and_entry_date(monkeypatch):
    from duanxian import risk,at_risk
    monkeypatch.setattr(at_risk,'load_equity_base',lambda:10000)
    rows=[{'code':'600000','date':'2026-09-08','settled':{'has_fills':True,'closed':True,'first_buy':'2026-09-01','last_sell':'2026-09-07','realized_pnl':0}}]*2
    eq=risk.equity_curve(rows)
    assert '全部持平' in risk.render({'equity':eq,'trade_count':2})
    rules={**risk.DEFAULT_RULES,'max_trades_per_day':1}
    hits=[x for x in risk.violations(rows,rules)['violations'] if x['rule']=='max_trades_per_day']
    assert hits[0]['date']=='2026-09-01'


def test_risk_reads_all_records_and_read_error_is_visible(monkeypatch):
    from duanxian import risk,at_risk,journal
    seen=[]
    def listing(limit=200):
        seen.append(limit);return {'trades':[],'total':0}
    monkeypatch.setattr(journal,'list_trades',listing);monkeypatch.setattr(risk,'load_rules',lambda:dict(risk.DEFAULT_RULES))
    risk.report();at_risk.report()
    assert seen==[None,None]
    def broken(**kw):raise ValueError('日志损坏')
    monkeypatch.setattr(journal,'list_trades',broken)
    assert '日志损坏' in at_risk.render(at_risk.report())


def test_modes_invalid_dates_cannot_be_used_for_comparison(tmp_path,monkeypatch):
    from duanxian import journal
    monkeypatch.setattr(modes,'_PATH',str(tmp_path/'cards.json'));monkeypatch.setattr(modes,'_DIR',str(tmp_path))
    modes.save_card({'name':'验收','playbook':'打板','setup':'v1'})
    monkeypatch.setattr(journal,'list_trades',lambda **kw:{'trades':[{'date':'2026-09-31','playbook':'打板','pnl_pct':2}],'total':1})
    result=modes.performance()
    assert result['invalid_dates']==1 and result['truncated']


def test_unscored_record_with_bad_date_does_not_block_mode_statistics(tmp_path,monkeypatch):
    from duanxian import journal
    monkeypatch.setattr(modes,'_PATH',str(tmp_path/'cards.json'));monkeypatch.setattr(modes,'_DIR',str(tmp_path))
    modes.save_card({'name':'验收','playbook':'打板','setup':'v1'})
    monkeypatch.setattr(journal,'list_trades',lambda **kw:{'trades':[{'date':'bad','playbook':'打板','pnl_pct':None}],'total':1})
    result=modes.performance()
    assert result['invalid_dates']==0 and not result['truncated']


def test_corrupt_risk_configuration_is_visible_in_http_response(tmp_path,monkeypatch):
    from duanxian import risk,at_risk
    import server
    path=tmp_path/'broken.json';path.write_text('{broken')
    monkeypatch.setattr(risk,'_RULES_PATH',str(path));monkeypatch.setattr(at_risk,'_BASE_PATH',str(path))
    for response in (server.api_risk_rules(),server.api_get_equity_base(),server.api_risk_report()):
        assert response.status_code==500
        assert '损坏' in json.loads(response.body)['error']
    metadata = json.loads(server.api_risk_rules_schema().body)
    assert metadata['rules'] == {} and set(metadata['defaults']) == set(risk.DEFAULT_RULES)
    assert path.read_text()=='{broken'


def test_empty_yesterday_pool_is_not_a_complete_baseline(monkeypatch):
    from duanxian import theme_tree as tt,market_facts as mf
    row={'code':'000001','name':'甲','boards':1}
    monkeypatch.setattr(mf,'pools',lambda d:{'zt':[row] if d=='2026-09-08' else [],'zb':[],'dt':[]})
    monkeypatch.setattr(tt,'reasons_of',lambda d:({'1':'机器人'},None))
    result=tt.build('2026-09-08','2026-09-07')
    assert result['covered']==1
    assert result['themes'][0]['state']!='今日新出现'
    assert '基线不可用' in result['source_note']

@pytest.mark.parametrize('payload',['{broken', '[]', '{}', '{"commission_rate":false,"commission_min":5,"stamp_tax_rate":0,"transfer_fee_rate":0}', '{"commission_rate":0,"commission_min":NaN,"stamp_tax_rate":0,"transfer_fee_rate":0}', '{"commission_rate":0.2,"commission_min":5,"stamp_tax_rate":0,"transfer_fee_rate":0}'])
def test_corrupt_fee_config_cannot_silently_change_profit(payload,tmp_path,monkeypatch):
    from duanxian import journal
    path=tmp_path/'fees.json';path.write_text(payload)
    monkeypatch.setattr(journal,'_FEE_PATH',str(path));monkeypatch.setattr(journal,'_DIR',str(tmp_path))
    with pytest.raises(ValueError,match='原件保留'):journal.load_fees()
    fills=[{'side':'buy','date':'2026-09-07','price':10,'shares':100},{'side':'sell','date':'2026-09-08','price':9,'shares':100}]
    with pytest.raises(ValueError,match='原件保留'):journal._settle(fills)
    assert path.read_text()==payload
    journal.save_fees(dict(journal.DEFAULT_FEES,commission_min=1))
    assert journal.load_fees()['commission_min']==1
    assert journal.load_fees()['is_default'] is False


def test_missing_fees_is_distinct_from_broken_file(tmp_path,monkeypatch):
    from duanxian import journal
    monkeypatch.setattr(journal,'_FEE_PATH',str(tmp_path/'missing.json'))
    assert journal.load_fees()=={**journal.DEFAULT_FEES,'is_default':True}


def test_fee_error_route_keeps_metadata_available(tmp_path,monkeypatch):
    from duanxian import journal
    import server
    path=tmp_path/'fees.json';path.write_text('{broken')
    monkeypatch.setattr(journal,'_FEE_PATH',str(path))
    response=server.api_journal_fees()
    assert response.status_code==500 and '原件保留' in response.body.decode()
    schema=json.loads(server.api_journal_fees_schema().body)
    assert len(schema['labels'])==4 and path.read_text()=='{broken'

@pytest.mark.parametrize('fees',[None,{}, {'commission_min':1}])
def test_fee_save_api_rejects_incomplete_configuration(fees,tmp_path,monkeypatch):
    import server
    from duanxian import journal
    path=tmp_path/'fees.json';path.write_text('{broken')
    monkeypatch.setattr(journal,'_FEE_PATH',str(path));monkeypatch.setattr(server,'_origin_ok',lambda _:True)
    response=server.api_journal_save_fees(None,{'fees':fees})
    assert response.status_code==400 and path.read_text()=='{broken'


def test_missing_dates_and_invalid_dates_both_remain_visible(monkeypatch):
    from duanxian import risk,at_risk
    monkeypatch.setattr(at_risk,'load_equity_base',lambda:None)
    rows=[{'code':'600000','date':'2026-09-08','pnl_pct':2}, {'code':'bad','date':'2026-09-08'}]
    status=risk.violations(rows,dict(risk.DEFAULT_RULES))['rule_status']['max_positions']
    assert status.startswith('unavailable') and '缺成交日期' in status and '日期异常' in status

@pytest.mark.parametrize('body',[{},None,{'commission_min':1},dict(commission_rate=' ',commission_min=1,stamp_tax_rate=0,transfer_fee_rate=0)])
def test_fee_function_also_rejects_missing_fields(body,tmp_path,monkeypatch):
    from duanxian import journal
    p=tmp_path/'fees.json';p.write_text('{broken')
    monkeypatch.setattr(journal,'_FEE_PATH',str(p))
    with pytest.raises(ValueError):journal.save_fees(body)
    assert p.read_text()=='{broken'


def test_fee_damage_does_not_hide_existing_journal_history(tmp_path,monkeypatch):
    from duanxian import journal
    bad=tmp_path/'fees.json';bad.write_text('{broken')
    trades=tmp_path/'trades.json';trades.write_text(json.dumps({'schema':3,'trades':[{'id':'test','date':'2026-09-08','code':'600000','pnl_pct':2,'settled':{'realized_pnl':20}}]}))
    monkeypatch.setattr(journal,'_FEE_PATH',str(bad));monkeypatch.setattr(journal,'_PATH',str(trades))
    assert journal.list_trades()['trades'][0]['id']=='test'
    assert journal.stats()['overall']['net_pnl']==20
    assert bad.read_text()=='{broken'
