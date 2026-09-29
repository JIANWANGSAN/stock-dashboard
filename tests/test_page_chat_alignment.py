"""A3 page/report source binding and public tool boundaries, no live calls."""
import json
import time
from types import SimpleNamespace
import pytest
from review_agent.api import PageChatInput
from review_agent.evidence import EvidenceError
from review_agent.runtime import connection
from test_deepdive_alignment import manager


def request(provider="codex-private", allow_tools=False, rid="f" * 32):
    return PageChatInput(messages=[{"role":"user","content":"解释本报告"}], context="报告A的材料",
                         llm={"provider":provider,"model":"test-model"}, request_id=rid, allow_tools=allow_tools)


def submit(m, body):
    source, key = connection(body.llm.model_dump())
    with m.lock: row = m.page_chats.submit(body, source, key)
    if m.page_chats.worker: m.page_chats.worker.join(5)
    assert not m.page_chats.busy()
    return row


@pytest.mark.parametrize("provider", ["codex-private", "claude", "codebuddy"])
def test_page_chat_uses_selected_source_context_and_no_default_fallback(tmp_path, provider):
    m = manager(tmp_path); calls=[]
    def invoke(run, source, key, prompt, *args, **kwargs):
        calls.append((source,prompt,kwargs)); return '{"answer":"材料只支持当前观察。"}'
    m.runtime._invoke = invoke
    try:
        submit(m, request(provider)); submit(m, request(provider))
        row=m.page_chats.status("f"*32)
        assert row["status"] == "complete" and len(calls)==1
        assert calls[0][0]["provider"]==provider and "报告A的材料" in calls[0][1]
        assert calls[0][2]["text_only"] is True
        assert row["result"]["ai_source"]["provider"]==provider
    finally: m.shutdown()


def test_off_rejects_tool_request_without_fetch(tmp_path, monkeypatch):
    m=manager(tmp_path)
    m.runtime._invoke=lambda *a,**k:'{"tool":"query_quote","args":{"codes":["600519"]}}'
    def forbidden(*a,**k): raise AssertionError("OFF must not fetch")
    monkeypatch.setattr('review_agent.public_worker.fetch_public',forbidden)
    try:
        submit(m,request())
        assert m.page_chats.snapshot()["status"]=="failed"
        assert "工具范围" in m.page_chats.snapshot()["error"]
    finally:m.shutdown()


def test_on_preserves_public_tool_and_exact_receipt(tmp_path,monkeypatch):
    m=manager(tmp_path); outputs=iter(['{"tool":"query_quote","args":{"codes":["600519"]}}','{"answer":"本次行情仅作公开资料观察。"}'])
    m.runtime._invoke=lambda *a,**k:next(outputs)
    calls=[]
    def fetch(name,args,*a):calls.append((name,args));return {"source":"synthetic", "price":100}
    monkeypatch.setattr('review_agent.public_worker.fetch_public',fetch)
    try:
        submit(m,request(allow_tools=True))
        row=m.page_chats.snapshot()
        assert row["status"]=="complete" and calls==[("query_quote",[{"codes":["600519"]}])]
        receipt=json.loads((m.page_chats.directory/('f'*32)/'evidence-1.json').read_text())
        assert receipt["value"]["price"]==100 and receipt["sha256"]
        assert row["result"]["rounds"]==2
    finally:m.shutdown()


def test_cancel_before_acceptance_never_bills(tmp_path):
    m=manager(tmp_path)
    m.runtime._invoke=lambda *a,**k:pytest.fail("cancelled before start")
    try:
        with m.lock:m.page_chats.cancel('f'*32)
        result=submit(m,request())
        assert result["status"]=="cancelled" and not m.page_chats.busy()
    finally:m.shutdown()


@pytest.mark.parametrize("name,args", [('query_quote', {'codes':['../auth']}),('query_news',{'code':'600519','url':'https://evil.test'}),('query_global_stock',{'symbol':'file:///etc/passwd'}),('unknown',{})])
def test_public_tool_args_rejected_before_import_or_network(name,args):
    from review_agent.public_worker import query_public
    with pytest.raises(ValueError):query_public(name,args)


def test_public_worker_cancel_reaps_real_sleep_process(tmp_path,monkeypatch):
    import subprocess, sys, threading
    from pathlib import Path
    from review_agent.public_worker import fetch_public
    from duanxian.llm_errors import LlmConfigError
    original=subprocess.Popen
    processes=[]
    fake=tmp_path/'sleep.py'; fake.write_text('import time\ntime.sleep(30)\n')
    def launch(argv,**kwargs):
        p=original([*argv[:5],sys.executable,str(fake)],**kwargs);processes.append(p);return p
    monkeypatch.setattr('review_agent.public_worker.subprocess.Popen',launch)
    start=time.monotonic()
    def check():
        if time.monotonic()-start>.3:raise LlmConfigError('cancelled')
        return 5
    with pytest.raises(LlmConfigError,match='cancelled'):fetch_public('resolve',['600519'],tmp_path,check)
    assert time.monotonic()-start<2 and processes[0].poll() is not None
    assert not list(tmp_path.glob('worker-*.jsonl'))


def test_dd_llm_directory_failure_is_safe_configuration_error(tmp_path,monkeypatch):
    from review_agent.daily import DailyLLM
    from duanxian.llm_errors import LlmConfigError
    from pathlib import Path
    import threading
    llm=DailyLLM(None,{},'',tmp_path,'2026-09-07',threading.Event(),lambda:20)
    def fail(*a,**k):raise OSError('secret-path-or-key')
    monkeypatch.setattr(Path,'mkdir',fail)
    with pytest.raises(LlmConfigError) as exc:llm.invoke('test')
    assert 'secret-path-or-key' not in str(exc.value)
