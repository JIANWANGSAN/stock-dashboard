import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import signal
import time

import pytest

from scripts import manage

REAL_CONNECT = socket.socket.connect


@pytest.mark.parametrize("stdout,code,expected", [
    ('ASTOCK_IMPORTS=[]', 0, True),
    ('ASTOCK_IMPORTS=["akshare: ModuleNotFoundError"]', 0, False),
    ('ASTOCK_IMPORTS=[]', 1, False),
    ('unrelated output', 0, False),
    ('ASTOCK_IMPORTS={}', 0, False),
])
def test_dependency_diagnostic_requires_completed_explicit_result(tmp_path, monkeypatch, stdout, code, expected):
    monkeypatch.setattr(manage.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], code, stdout, "SECRET_CANARY"))
    ok, action = manage.dependency_status(tmp_path)
    assert ok is expected
    assert "SECRET_CANARY" not in action


def test_dependency_timeout_is_not_reported_as_missing_package(tmp_path, monkeypatch):
    def timeout(*a, **k):
        raise subprocess.TimeoutExpired([], 60)
    monkeypatch.setattr(manage.subprocess, "run", timeout)
    ok, action = manage.dependency_status(tmp_path)
    assert not ok and "一分钟" in action


def layout(root):
    for name in ("requirements.txt", "runtime/package.json", "runtime/package-lock.json",
                 "frontend/package-lock.json", "frontend/src/main.ts", "frontend/dist/index.html"):
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}")


def test_source_changes_invalidate_install_but_generated_files_do_not(tmp_path):
    layout(tmp_path)
    original = manage.fingerprint(tmp_path)
    (tmp_path / "frontend/dist/index.html").write_text("built")
    (tmp_path / "frontend/temp.tsbuildinfo").write_text("generated")
    assert manage.fingerprint(tmp_path) == original
    (tmp_path / "frontend/src/main.ts").write_text("changed")
    assert manage.fingerprint(tmp_path) != original


def test_doctor_missing_and_outdated_install_and_dependency_conflict(tmp_path, monkeypatch):
    layout(tmp_path)
    monkeypatch.setattr(manage, "probe", lambda *a, **k: True)
    monkeypatch.setattr(manage, "dependency_status", lambda _: (True, ""))
    monkeypatch.setattr(manage.shutil, "which", lambda _: "/npm")
    assert not manage.doctor(tmp_path)[-1]["ok"]
    (tmp_path / ".local").mkdir()
    (tmp_path / ".local/setup.json").write_text(json.dumps({"fingerprint": manage.fingerprint(tmp_path)}))
    assert all(c["ok"] for c in manage.doctor(tmp_path))
    monkeypatch.setattr(manage, "probe", lambda argv, *a, **k: "check" not in argv)
    assert not next(c for c in manage.doctor(tmp_path) if c["name"] == "依赖兼容性")["ok"]
    (tmp_path / "requirements.txt").write_text("changed")
    assert not manage.doctor(tmp_path)[-1]["ok"]


@pytest.mark.parametrize("value", [[], None, "broken", 42, {"fingerprint": []}])
def test_corrupt_marker_can_be_repaired(tmp_path, monkeypatch, value):
    layout(tmp_path)
    (tmp_path / ".local").mkdir()
    marker = tmp_path / ".local/setup.json"
    marker.write_text(json.dumps(value))
    monkeypatch.setattr(manage, "probe", lambda *a, **k: True)
    monkeypatch.setattr(manage, "dependency_status", lambda _: (True, ""))
    monkeypatch.setattr(manage.shutil, "which", lambda _: "/npm")
    monkeypatch.setattr(manage, "prerequisites", lambda _: None)
    monkeypatch.setattr(manage, "run", lambda *a, **k: None)
    assert not manage.doctor(tmp_path)[-1]["ok"]
    manage.setup(tmp_path)
    assert manage.doctor(tmp_path)[-1]["ok"]


def test_failed_install_never_marks_success(tmp_path, monkeypatch):
    layout(tmp_path)
    monkeypatch.setattr(manage, "prerequisites", lambda _: None)
    def fail(*a, **k):
        raise manage.SetupError("installation failed")
    monkeypatch.setattr(manage, "run", fail)
    with pytest.raises(manage.SetupError):
        manage.setup(tmp_path)
    assert not (tmp_path / ".local/setup.json").exists()


def test_port_collision_never_opens_other_service(tmp_path, monkeypatch):
    monkeypatch.setattr(manage, "doctor", lambda _: [{"name": "test", "ok": True, "action": "ok"}])
    def forbidden(*a, **k):
        pytest.fail("must not launch/open a different service")
    monkeypatch.setattr(manage.subprocess, "Popen", forbidden)
    monkeypatch.setattr(manage.webbrowser, "open", forbidden)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); sock.listen()
        with pytest.raises(manage.SetupError, match="占用"):
            manage.start(tmp_path, sock.getsockname()[1], True)


def test_recently_closed_port_can_be_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(manage, "doctor", lambda _: [{"name": "test", "ok": True, "action": "ok"}])
    with socket.socket() as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0)); server.listen()
        port = server.getsockname()[1]
        with socket.socket() as client:
            REAL_CONNECT(client, ("127.0.0.1", port))
            accepted, _ = server.accept()
            accepted.close()
            assert client.recv(1) == b""
    def reached_launch(*a, **k):
        raise manage.SetupError("port check passed")
    monkeypatch.setattr(manage.subprocess, "Popen", reached_launch)
    with pytest.raises(manage.SetupError, match="port check passed"):
        manage.start(tmp_path, port, False)


@pytest.mark.skipif(os.name != "posix", reason="POSIX launcher")
def test_real_http_ready_identity_and_interrupt_terminate_child(tmp_path, monkeypatch):
    # Run an actual child HTTP server. Doctor alone cannot prove readiness,
    # and a preexisting/other product's response must never open the browser.
    monkeypatch.setattr(manage, "doctor", lambda _: [{"name": "test", "ok": True, "action": "ok"}])
    def loopback_only(sock, address):
        assert address[0] == "127.0.0.1"
        return REAL_CONNECT(sock, address)
    monkeypatch.setattr(socket.socket, "connect", loopback_only)
    real_popen = subprocess.Popen
    children = []
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    code = '''
from http.server import BaseHTTPRequestHandler,HTTPServer
import json,os,sys
class H(BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200);self.end_headers()
  self.wfile.write(json.dumps({'service':'vibe-astock','launch_id':os.environ['ASTOCK_LAUNCH_ID'],'ready':True}).encode())
HTTPServer(('127.0.0.1',int(sys.argv[1])),H).serve_forever()
'''
    def spawn(argv, **kwargs):
        child = real_popen([sys.executable, "-u", "-c", code, str(port)], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(manage.subprocess, "Popen", spawn)
    def opened(url):
        assert not manage.healthy(url, "different-launch")
        assert children[0].poll() is None
        raise KeyboardInterrupt
    monkeypatch.setattr(manage.webbrowser, "open", opened)
    with pytest.raises(KeyboardInterrupt):
        manage.start(tmp_path, port, True, timeout=5)
    assert children[0].poll() is not None
    assert not manage.healthy(f"http://127.0.0.1:{port}", "different-launch")


@pytest.mark.skipif(os.name != "posix", reason="POSIX terminal hangup")
def test_terminal_hangup_stops_independent_service(tmp_path):
    # Enter through main(): a manual KeyboardInterrupt in start() does not test
    # OS signal handling when a Terminal window is closed.
    pidfile = tmp_path / "child.pid"
    code = '''
from scripts import manage
from pathlib import Path
import sys,subprocess
manage.ROOT=Path(sys.argv[1])
manage.prerequisites=lambda _: None
manage.doctor=lambda _: [{'name':'test','ok':True,'action':'ok'}]
manage.healthy=lambda *a: True
real=subprocess.Popen
def spawn(argv,**kwargs):
 p=real([sys.executable,'-c','import time;time.sleep(120)'],**kwargs)
 (manage.ROOT/'child.pid').write_text(str(p.pid))
 return p
manage.subprocess.Popen=spawn
sys.argv=['manage','start','--no-browser','--port',sys.argv[2]]
raise SystemExit(manage.main())
'''
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    proc = subprocess.Popen([sys.executable, "-c", code, str(tmp_path), str(port)], cwd=manage.ROOT,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    child = None
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not pidfile.exists() and proc.poll() is None:
            time.sleep(.02)
        assert pidfile.exists()
        child = int(pidfile.read_text())
        proc.send_signal(signal.SIGHUP)
        assert proc.wait(timeout=20) == 130
        with pytest.raises(ProcessLookupError):
            os.kill(child, 0)
    finally:
        manage.stop(proc)
        if child is not None:
            try:
                os.killpg(child, signal.SIGKILL)
            except ProcessLookupError:
                pass
