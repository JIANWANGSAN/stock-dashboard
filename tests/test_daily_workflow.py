"""Failure status and later-day evidence must survive the actual entry points."""
import json
import sys

import pytest

import main as cli
from duanxian import review_store
from review_agent.evidence import build_bundle, ToolSession, validate_answer
from review_agent.followup import Followup
from review_agent.store import Store


@pytest.mark.parametrize("metric,group,values,phrase", [
    ("deep_loss_count", "market_facts", {"loss_effect": {"deep_loss_5_count": 8}}, "非全市场深跌家数"),
    ("money_effect_median", "emotion_metrics", {"money_effect": {"median": -1.78}}, "非全市场中位数"),
    ("theme_concentration", "market_facts", {"theme_structure": {"concentration": .103}}, "行业分组不等同于概念题材"),
])
def test_metric_scope_survives_read_compare_and_replay(tmp_path, metric, group, values, phrase):
    for day in ("2026-09-03", "2026-09-04"):
        (tmp_path / f"{day}.json").write_text(json.dumps({"target_date": day, group: values}))
    bundle = build_bundle(tmp_path, "2026-09-04")
    tools = ToolSession(bundle, tmp_path / "tools.jsonl")
    result = tools.compare_metric(metric, "2026-09-03", "2026-09-04")
    assert phrase in result["note"]
    assert "跨日样本成员及覆盖可能不同" in result["note"]
    assert all(phrase in e["note"] for e in tools.read_evidence(result["inputs"]))
    tools.submit_answer("complete", [{"text": "样本读数持平。", "citations": [result["id"]]}], [])
    replay = ToolSession(bundle, tools.log)
    replay.replay()
    assert all(phrase in e["note"] for e in replay.answer["evidence"])


@pytest.mark.parametrize("exited", [True, False])
def test_cleanup_permission_failure_only_ignored_after_child_exit(monkeypatch, exited):
    import os
    from review_agent.runtime import stop_process
    if os.name != "posix":
        pytest.skip("POSIX process groups")
    class Child:
        pid = 987654
        waited = False
        def poll(self):
            return 0 if exited else None
        def wait(self, timeout):
            self.waited = True
            if not exited:
                import subprocess
                raise subprocess.TimeoutExpired("child", timeout)
            return 0
    def denied(*_):
        raise PermissionError("already vanished or permission denied")
    monkeypatch.setattr(os, "killpg", denied)
    child = Child()
    if exited:
        stop_process(child)
        assert child.waited
    else:
        with pytest.raises(PermissionError):
            stop_process(child)


def test_failed_daily_review_exits_nonzero_and_preserves_good_latest(tmp_path, monkeypatch):
    monkeypatch.setattr(review_store, "DIR", str(tmp_path))
    monkeypatch.setattr(review_store, "REJECT_DIR", str(tmp_path / "_rejected"))
    original = {"target_date": "2026-09-02", "focus_md": "原有完整报告" * 100}
    review_store.save(original, "2026-09-02")
    monkeypatch.setattr(sys, "argv", ["main.py", "2026-09-03"])
    monkeypatch.setattr(cli, "run", lambda _: ({"tomorrow_focus": ""}, {"warnings": ["模型限流"]}))
    monkeypatch.setattr(cli.reflection, "auto_evaluate_prior", lambda _: None)
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert json.loads((tmp_path / "latest.json").read_text()) == original
    assert list((tmp_path / "_rejected").glob("2026-09-03*.json"))


def test_new_review_checks_keep_baseline_and_prior_result_after_source_change(tmp_path):
    reviews = tmp_path / "reviews"; reviews.mkdir()
    def write(day, count):
        (reviews / f"{day}.json").write_text(json.dumps({"target_date": day,
          "emotion_metrics": {"promotion": {"limit_up_count": count}}}))
    write("2026-09-03", 40)
    store = Store(tmp_path / "state")
    bundle = build_bundle(reviews, "2026-09-03")
    turn, _ = store.start("2026-09-03", "观察", "d" * 32, {}, lambda: bundle)
    tools = ToolSession(bundle, tmp_path / "tools.jsonl")
    ev = next(e for e in bundle["evidence"] if e.get("metric") == "limit_up_count")
    tools.read_evidence([ev["id"]])
    store.finish(turn["id"], result=validate_answer({"status": "complete", "findings": [
        {"text": "记录涨停家数。", "citations": [ev["id"]]}], "gaps": []}, tools))
    follow = Followup(store, reviews)
    observation = follow.add(turn["id"], "limit_up_count", "下降")
    assert follow.check(observation["id"], "2026-09-04")["status"] == "数据不足"
    write("2026-09-04", 30)
    result = follow.check(observation["id"], "2026-09-04")
    assert result["actual"] == "下降" and result["verified"] is True
    write("2026-09-03", 999); write("2026-09-04", 45)
    restored = Followup(Store(tmp_path / "state"), reviews)
    checks = restored.list(turn["conversation_id"])[0]["checks"]
    assert checks[0]["baseline"]["value"] == 40
    assert checks[0]["current"]["value"] == 30
    assert checks[1]["status"] == "数据不足"
    next_result = restored.check(observation["id"], "2026-09-04")
    assert next_result["actual"] == "持平" and next_result["verified"] is False
    assert next_result["current"]["source_sha256"] != result["current"]["source_sha256"]


def test_cleanup_accepts_child_exiting_during_reap(monkeypatch):
    import os
    from review_agent.runtime import stop_process
    if os.name != "posix":
        pytest.skip("POSIX process groups")
    class Child:
        pid = 987654
        def poll(self): return None
        def wait(self, timeout): return 0
    def denied(*args): raise PermissionError()
    monkeypatch.setattr(os, "killpg", denied)
    stop_process(Child())
