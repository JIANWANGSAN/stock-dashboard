"""A2 transport/mode contracts: no real supplier calls or user credentials."""
import json
import threading
import uuid
from pathlib import Path
import pytest
from review_agent.runtime import Runtime, connection, config_for
from review_agent.api import Manager, TurnInput
from review_agent.store import Store
from review_agent.evidence import EvidenceError


def test_subscription_source_not_codex_fallback():
    for provider in ['claude','codebuddy']:
        assert connection({'provider':provider,'model':'default'}) == ({'provider':provider,'model':'default'}, '')


def test_explicit_responses_api_and_secrets():
    source,key=connection({'provider':'api-compatible','model':'custom-v1','baseURL':'https://example.com/v1','apiKey':'private-test'})
    assert source=={'provider':'api-compatible','model':'custom-v1','baseURL':'https://example.com/v1'}
    assert key=='private-test' and 'private-test' not in json.dumps(source)
    for url in ['http://example.com','https://user:pw@example.com','https://example.com/?key=secret','https://example.com:123/v1']:
        with pytest.raises(EvidenceError):connection({'provider':'api-compatible','model':'m','baseURL':url,'apiKey':'private-test'})


def test_direct_does_not_read_review_bundle(tmp_path,monkeypatch):
    import review_agent.api as api
    def forbidden(*args):raise AssertionError('direct read business bundle')
    monkeypatch.setattr(api,'build_bundle',forbidden)
    runtime=Runtime(tmp_path/'state')
    seen=[]
    def invoke(*args,**kw):seen.append(kw);return '普通回答，不曾联网'
    monkeypatch.setattr(runtime,'_invoke',invoke)
    manager=Manager(Store(runtime.root),tmp_path/'reviews',runtime)
    body=TurnInput(anchor='2026-09-04',question='解释行业与题材',request_id=uuid.uuid4().hex,
        llm={'provider':'codex-private','model':'test'},scope={'mode':'direct','page':'首页'})
    try:
        turn=manager.submit(body)
        if manager.active:manager.active[2].join(3)
        result=manager.store.turn(turn['id'])
        assert result['status']=='complete' and result['result']['mode']=='direct'
        assert seen==[{'text_only':True,'ordinary':True}]
        assert manager.store.conversation(turn['conversation_id'])['dates']==[]
        assert manager.submit(body)['id']==turn['id'] and len(seen)==1
        # New task cannot change source/mode inside an existing conversation.
        body.conversation_id=turn['conversation_id'];body.request_id=uuid.uuid4().hex;body.scope.mode='agent'
        with pytest.raises(EvidenceError,match='范围'):manager.submit(body)
    finally:manager.shutdown()


def test_direct_rejects_network(tmp_path):
    runtime=Runtime(tmp_path/'state');manager=Manager(Store(runtime.root),tmp_path/'reviews',runtime)
    try:
        with pytest.raises(EvidenceError,match='普通对话'):
            manager.submit(TurnInput(anchor='2026-09-04',question='q',request_id=uuid.uuid4().hex,
              llm={'provider':'codex-private','model':'m'},scope={'mode':'direct','allow_network':True}))
    finally:manager.shutdown()


def test_local_subscription_runs_selected_bridge_without_codex(tmp_path,monkeypatch):
    import review_agent.runtime as runtime_module
    import review_agent.subscription_bridge as bridge
    monkeypatch.setattr(runtime_module,'engine_command',lambda:(_ for _ in ()).throw(AssertionError('codex fallback')))
    captured=[]
    monkeypatch.setattr(bridge,'invoke',lambda *a,**k:captured.append((a,k)) or 'done')
    runtime=Runtime(tmp_path)
    for provider in ['claude','codebuddy']:
        assert runtime._invoke(tmp_path,{'provider':provider,'model':'default'},'','q',threading.Event(),lambda _:None,10,text_only=True)=='done'
        assert captured[-1][0][2]['provider']==provider and captured[-1][1]['tools']==()
        runtime._invoke(tmp_path,{'provider':provider,'model':'default'},'','q',threading.Event(),lambda _:None,10)
        assert 'submit_answer' in captured[-1][1]['tools']


def test_probe_request_identity_survives_restart(tmp_path, monkeypatch):
    from review_agent.access import Access
    runtime = Runtime(tmp_path)
    calls=[]
    def probe(self, source, key):
        calls.append(key)
        self._update(status='complete',source=source,message='tested')
    monkeypatch.setattr(Access,'_probe',probe)
    access=Access(runtime);rid=uuid.uuid4().hex;source={'provider':'claude','model':'default'}
    first=access.start('probe',source,'TEST_SECRET',request_id=rid)
    access.worker.join(2)
    assert access.start('probe',source,'TEST_SECRET',request_id=rid)['status']=='complete'
    assert Access(runtime).start('probe',source,'TEST_SECRET',request_id=rid)['id']==first['id']
    assert calls==['TEST_SECRET']
    assert 'TEST_SECRET' not in (tmp_path/'access-tests.json').read_text()
    with pytest.raises(EvidenceError):access.start('probe',source,'different',request_id=rid)


def test_workspace_conversation_lists_are_separate(tmp_path):
    store=Store(tmp_path)
    for mode,page in [('direct','首页'),('agent','首页'),('agent','')]:
        context={'mode':mode,'page':page} if mode=='direct' else {'page':page}
        bundle={'context':context,'dates':[],'revision':'test'}
        turn,_=store.start('2026-09-02','q',uuid.uuid4().hex,{'provider':'claude','model':'default'},lambda:bundle)
        store.finish(turn['id'],result={'status':'complete','text':'x'})
    assert len(store.list_conversations('2026-09-02'))==1
    assert len(store.list_conversations('', 'direct','首页'))==1
    assert len(store.list_conversations('', 'agent','首页'))==1


def test_probe_restart_uses_time_not_random_identifier(tmp_path,monkeypatch):
    from review_agent.access import Access
    def probe(self,source,key):self._update(status='complete' if source['model']=='older' else 'failed',source=source)
    monkeypatch.setattr(Access,'_probe',probe)
    r=Runtime(tmp_path);access=Access(r)
    access.start('probe',{'provider':'claude','model':'older'},request_id='f'*32);access.worker.join(2)
    access.start('probe',{'provider':'claude','model':'newer'},request_id='a'*32);access.worker.join(2)
    restored=Access(r).snapshot()
    assert restored['id']=='a'*32 and restored['status']=='failed'
