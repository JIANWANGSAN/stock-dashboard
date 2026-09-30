import json
import pytest
from review_agent.evidence import build_bundle, ToolSession, validate_answer, EvidenceError
from review_agent.store import Store
from review_agent.followup import Followup


def snapshot(root, day, count):
    (root/f'{day}.json').write_text(json.dumps({'target_date':day,'emotion_metrics':{'promotion':{'limit_up_count':count}},'market_facts':{}}))


def test_condition_links_to_cited_anchor_evidence_and_survives_restart(tmp_path, monkeypatch):
    reviews=tmp_path/'reviews';reviews.mkdir()
    snapshot(reviews,'2026-09-01',30);snapshot(reviews,'2026-09-02',35);snapshot(reviews,'2026-09-03',36)
    store=Store(tmp_path/'state');bundle=build_bundle(reviews,'2026-09-01')
    turn,_=store.start('2026-09-01','观察','a'*32,{},lambda:bundle)
    session=ToolSession(bundle,tmp_path/'tools.jsonl');e=next(e for e in bundle['evidence'] if e.get('metric')=='limit_up_count')
    session.read_evidence([e['id']]);result=validate_answer({'status':'complete','findings':[{'text':'观察涨停家数。','citations':[e['id']]}],'gaps':[]},session)
    store.finish(turn['id'],result=result)
    follow=Followup(store,reviews);item=follow.add(turn['id'],'limit_up_count','上升')
    assert follow.add(turn['id'],'limit_up_count','上升')['id']==item['id']
    with pytest.raises(EvidenceError):follow.add(turn['id'],'highest_board','上升')
    assert follow.check(item['id'],'2026-09-02')['actual']=='持平'
    assert follow.check(item['id'],'2026-09-03')['verified'] is True
    assert follow.check(item['id'],'2026-09-04')['verified'] is None
    with pytest.raises(EvidenceError):follow.check(item['id'],'2026-09-01')
    snapshot(reviews,'2026-09-01',999)
    resumed=Followup(Store(tmp_path/'state'),reviews)
    listed=resumed.list(turn['conversation_id'])
    assert listed[0]['baseline']['value']==30 and len(listed[0]['checks'])==3
    assert resumed.check(item['id'],'2026-09-03')['baseline']['value']==30
    from duanxian.verification import METRICS
    monkeypatch.setattr(METRICS[0], 'eps', 500)
    assert resumed.check(item['id'],'2026-09-03')['verified'] is True
    assert resumed.get(item['id'])['rule']['threshold']==5
    assert next(r for r in resumed.menu() if r['metric']=='broken_rate')['threshold_unit']=='个百分点'



def test_api_guards_scope_and_observations(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from review_agent.api import Manager,create_router
    from review_agent.runtime import Runtime
    m=Manager(Store(tmp_path/'state'),tmp_path,Runtime(tmp_path/'state'))
    app=FastAPI();app.include_router(create_router(m,'local-secret'))
    try:
        c=TestClient(app,base_url='http://127.0.0.1')
        assert c.get('/api/review-agent/observations?conversation_id='+ 'a'*32).status_code==401
        c.headers['authorization']='Bearer local-secret'
        assert c.post('/api/review-agent/observations',json={'turn_id':'a'*32,'metric':'x','direction':'up'}).status_code==400
        assert c.post('/api/review-agent/access/probe',json={'llm':{'model':'x','provider':'openai','apiKey':{'SECRET_CANARY':'secret'}}}).status_code==422
        assert 'SECRET_CANARY' not in c.post('/api/review-agent/access/probe',json={'llm':{'model':'x','provider':'openai','apiKey':{'SECRET_CANARY':'secret'}}}).text
    finally:m.shutdown()
