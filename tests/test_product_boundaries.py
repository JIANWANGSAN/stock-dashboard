"""Public web product boundaries; synthetic responses, no provider calls or user data."""
import json
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from review_agent.api import Manager, TurnInput
from review_agent.daily import check_report_text
from review_agent.evidence import EvidenceError, validate_answer
from review_agent.runtime import Runtime
from review_agent.store import Store
from duanxian.llm_errors import LlmConfigError


@pytest.mark.parametrize('text', [
    '明天买入这只股票。', '建议持仓三成。', '推荐个股供你建仓。',
    'You should buy this stock tomorrow.', 'I recommend selling this stock.',
    '可以低吸这只股票。',
    '买入这只股票', '卖出该标的', '立即建仓',
])
def test_direct_recommendation_never_saved_as_success(tmp_path, monkeypatch, text):
    runtime = Runtime(tmp_path/'state')
    monkeypatch.setattr(runtime, '_invoke', lambda *a, **kw: text)
    manager = Manager(Store(runtime.root), tmp_path/'reviews', runtime)
    try:
        turn = manager.submit(TurnInput(anchor='2026-09-04', question='解释资料', request_id=uuid.uuid4().hex,
            llm={'provider':'codex-private','model':'synthetic'}, scope={'mode':'direct','page':'首页'}))
        if manager.active:
            manager.active[2].join(3)
        result = manager.store.turn(turn['id'])
        assert result['status'] == 'failed'
        assert text not in json.dumps(result, ensure_ascii=False)
    finally:
        manager.shutdown()


@pytest.mark.parametrize('text', ['明天买入这只股票。', 'You should buy this stock tomorrow.', '可以低吸这只股票。',
                                  '买入这只股票', '卖出该标的', '立即建仓'])
def test_reports_reject_explicit_actions(text):
    with pytest.raises(LlmConfigError):
        check_report_text(text)


@pytest.mark.parametrize('field', ['findings', 'gaps'])
def test_evidence_answers_share_action_check(field):
    payload = {'status':'incomplete','findings':[{'text':'资料不足。','citations':[]}], 'gaps':['资料未获取。']}
    if field == 'findings':
        payload['findings'][0]['text'] = '明天买入这只股票。'
    else:
        payload['gaps'] = ['明天买入这只股票。']
    session = SimpleNamespace(used=set(), records={}, bundle={'gaps':[]}, network_gaps=[])
    with pytest.raises(EvidenceError, match='边界'):
        validate_answer(payload, session)


@pytest.mark.parametrize('text', [
    '我不能替你做交易决定，可以整理资料与风险。',
    '追涨杀跌描述追随涨跌进行交易的行为。',
    '历史回测规则：均线上穿买入、下穿卖出；这是假设模拟，不能代表未来收益。',
    'The historical buy-and-hold benchmark had no trades after entry.',
    '昨日涨停样本的晋级率不能解释为个股上涨概率。',
    '开盘后买入，收盘前卖出。',
    '买入这只标的后持有五日。',
    '当时仓位为三成。',
])
def test_research_education_and_historical_simulation_remain_available(text):
    check_report_text(text)


@pytest.mark.parametrize('ordinary,task_kind', [(True,'daily'),(False,'daily'),(False,'stock'),(False,'page')])
@pytest.mark.parametrize('provider', ['claude', 'codebuddy'])
def test_policy_reaches_subscription_provider(tmp_path, monkeypatch, ordinary, task_kind, provider):
    from review_agent import subscription_bridge
    seen = []
    def invoke(runtime, run, source, prompt, instructions, *args, **kwargs):
        seen.append(instructions)
        return '资料不足。'
    monkeypatch.setattr(subscription_bridge, 'invoke', invoke)
    Runtime(tmp_path)._invoke(tmp_path, {'provider':provider,'model':'default'}, '', '材料',
        threading.Event(), lambda _: None, 1, text_only=True, ordinary=ordinary, task_kind=task_kind)
    assert '主动通知' in seen[0] and '交易决策' in seen[0] and '历史模拟' in seen[0]


def test_web_source_excludes_native_client_but_preserves_launcher():
    root = Path(__file__).resolve().parents[1]
    assert not (root/'packaging/macos').exists()
    assert (root/'scripts/start').is_file()
    assert (root/'启动 Vibe AStock.command').is_file()


def test_startup_backup_does_not_read_research_default(tmp_path, monkeypatch):
    import server
    monkeypatch.delenv('VR_DATA_DIR', raising=False)
    monkeypatch.setenv('HOME', str(tmp_path))
    checked = []
    monkeypatch.setattr(server.os.path, 'isfile', lambda path: checked.append(path) or False)
    server._guard_vr_userdata()
    assert checked == [str(tmp_path/'.vibe-astock-agent/market-data/portfolio.json')]


@pytest.mark.parametrize('override', [None, 'custom-market-data'])
def test_watch_storage_uses_astock_default_or_explicit_override(tmp_path, override):
    import os
    import subprocess
    import sys
    env = {**os.environ, 'HOME':str(tmp_path)}
    # Production mounts the vendored vr module directory on sys.path.
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1]/'vr')
    env.pop('VR_DATA_DIR', None)
    expected = tmp_path/'.vibe-astock-agent/market-data/monitor'
    if override:
        env['VR_DATA_DIR'] = str(tmp_path/override)
        expected = tmp_path/override/'monitor'
    output = subprocess.check_output([sys.executable, '-c',
        'from vr.watchtower import _DATA_DIR; print(_DATA_DIR)'], env=env, text=True)
    assert output.strip() == str(expected)
