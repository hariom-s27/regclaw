"""Tests for scripts/s10c2_conn_probe.py (diagnostic connection probe)."""
import json
import httpx
import pytest

from scripts import s10c2_conn_probe as probe

FAKE_ENDPOINT = "https://fake-resource.openai.azure.com"
FAKE_KEY = "sk-fake-secret-key-98765"


def fake_getaddrinfo(host, port):
    return [(2, 1, 0, "", ("127.0.0.1", port))]


def test_out_folder_exists_refuses(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", FAKE_ENDPOINT)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", FAKE_KEY)
    out_dir = tmp_path / "already_exists"
    out_dir.mkdir()
    code = probe.main(["--out", str(out_dir), "--label", "test"], getaddrinfo_fn=fake_getaddrinfo)
    assert code == 2
    captured = capsys.readouterr()
    assert "refused: output folder exists" in captured.out
    assert not list(out_dir.iterdir())


def test_missing_env_refuses(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    out_dir = tmp_path / "probe_out"
    code = probe.main(["--out", str(out_dir), "--label", "test"])
    assert code == 2
    captured = capsys.readouterr()
    assert "refused: missing env" in captured.out
    assert not out_dir.exists()


def test_redacts_api_key_in_exception_cause_chain(tmp_path, monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", FAKE_ENDPOINT)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", FAKE_KEY)
    out_dir = tmp_path / "probe_out"

    calls = [0]

    def handler(request):
        calls[0] += 1
        cb = request.extensions.get("trace")
        if cb:
            cb("connection.connect_tcp.started", {})
        if calls[0] == 1:
            if cb:
                cb("connection.connect_tcp.complete", {})
            return httpx.Response(200)
        raise RuntimeError(f"Connection leak error with {FAKE_KEY}")

    transport = httpx.MockTransport(handler)
    code = probe.main(
        ["--out", str(out_dir), "--label", "test", "--soak-minutes", "0.05", "--soak-interval-s", "1", "--skip-idle"],
        transport_factory=lambda **kwargs: transport, sleep_fn=lambda s: None, getaddrinfo_fn=fake_getaddrinfo,
    )
    assert code == 0
    for f in out_dir.rglob("*"):
        if f.is_file():
            text = f.read_text(encoding="utf-8")
            assert FAKE_KEY not in text
            if f.name == "events.jsonl":
                assert "[REDACTED]" in text


def test_mock_transport_connect_error_logged(tmp_path, monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", FAKE_ENDPOINT)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", FAKE_KEY)
    out_dir = tmp_path / "probe_out"

    calls = [0]

    def handler(request):
        calls[0] += 1
        cb = request.extensions.get("trace")
        if cb:
            cb("connection.connect_tcp.started", {})
        if calls[0] == 1:
            if cb:
                cb("connection.connect_tcp.complete", {})
            return httpx.Response(200)
        raise httpx.ConnectError("connection refused")

    transport = httpx.MockTransport(handler)
    code = probe.main(
        ["--out", str(out_dir), "--label", "test", "--soak-minutes", "0.05", "--soak-interval-s", "1", "--skip-idle"],
        transport_factory=lambda **kwargs: transport, sleep_fn=lambda s: None, getaddrinfo_fn=fake_getaddrinfo,
    )
    assert code == 0
    events = [json.loads(line) for line in (out_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    err_events = [e for e in events if e["outcome"] == "error"]
    assert len(err_events) > 0
    assert err_events[0]["error_type"] == "ConnectError"
    assert len(err_events[0]["cause_chain"]) > 0


def test_p2_consecutive_failures_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", FAKE_ENDPOINT)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", FAKE_KEY)
    out_dir = tmp_path / "probe_out"

    sequence = [200, 200, 500, 500, 200, 500]
    idx = [0]

    def handler(request):
        code_resp = sequence[idx[0]]
        idx[0] += 1
        cb = request.extensions.get("trace")
        if cb:
            cb("connection.connect_tcp.started", {})
            cb("connection.connect_tcp.complete", {})
        return httpx.Response(code_resp)

    transport = httpx.MockTransport(handler)
    code = probe.main(
        ["--out", str(out_dir), "--label", "test", "--soak-minutes", "0.05", "--soak-interval-s", "0.6", "--skip-idle"],
        transport_factory=lambda **kwargs: transport, sleep_fn=lambda s: None, getaddrinfo_fn=fake_getaddrinfo,
    )
    assert code == 0
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["P2"]["n"] == 5
    assert summary["P2"]["failed"] == 3
    assert summary["P2"]["longest_consecutive_failures"] == 2


def test_idle_schedule_sleep_intervals(tmp_path, monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", FAKE_ENDPOINT)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", FAKE_KEY)
    out_dir = tmp_path / "probe_out"

    slept = []

    def mock_sleep(s):
        slept.append(s)

    def handler(request):
        cb = request.extensions.get("trace")
        if cb:
            cb("connection.connect_tcp.started", {})
            cb("connection.connect_tcp.complete", {})
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    code = probe.main(
        ["--out", str(out_dir), "--label", "test", "--skip-soak", "--idle-list", "30,90,180,300"],
        transport_factory=lambda **kwargs: transport,
        sleep_fn=mock_sleep,
        getaddrinfo_fn=fake_getaddrinfo,
    )
    assert code == 0
    import socket
    n_arms = 2 if hasattr(socket, "TCP_KEEPIDLE") else 1
    assert sorted(slept) == sorted([30, 90, 180, 300] * n_arms)


def test_p3_arm_exception_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", FAKE_ENDPOINT)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", FAKE_KEY)
    out_dir = tmp_path / "probe_out"
    ok_transport = httpx.MockTransport(lambda req: httpx.Response(200))

    def transport_factory(arm=None, socket_options=None, limits=None):
        if arm == "ka_on":
            raise RuntimeError(f"arm failed with {FAKE_KEY}")
        return ok_transport

    code = probe.main(
        ["--out", str(out_dir), "--label", "test", "--skip-soak", "--idle-list", "30"],
        transport_factory=transport_factory, sleep_fn=lambda s: None, getaddrinfo_fn=fake_getaddrinfo,
    )
    assert code == 0
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert "ka_on:error" in summary["P3"]
    assert summary["P3"]["ka_on:error"]["error_type"] == "RuntimeError"
    assert FAKE_KEY not in summary["P3"]["ka_on:error"]["message"]
    assert "ka_off:30" in summary["P3"]


def test_check_request_failure_exits_early(tmp_path, monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", FAKE_ENDPOINT)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", FAKE_KEY)
    out_dir = tmp_path / "probe_out"

    calls = [0]

    def handler(request):
        calls[0] += 1
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    code = probe.main(
        ["--out", str(out_dir), "--label", "test"],
        transport_factory=lambda **kwargs: transport, sleep_fn=lambda s: None, getaddrinfo_fn=fake_getaddrinfo,
    )
    assert code == 2
    assert calls[0] == 1
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["reason"] == "check_failed"
    assert summary["status"] == 404


def test_phase_at_failure_extraction():
    events = [
        ["connection.connect_tcp.started", 0.01],
        ["connection.connect_tcp.complete", 0.05],
        ["http11.receive_response_headers.started", 0.10],
    ]
    assert probe.phase_at_failure(events) == "http11.receive_response_headers.started"

    events_complete = [
        ["connection.connect_tcp.started", 0.01],
        ["connection.connect_tcp.complete", 0.05],
        ["http11.send_request_headers.started", 0.06],
        ["http11.send_request_headers.complete", 0.07],
    ]
    assert probe.phase_at_failure(events_complete) is None

    assert probe.phase_at_failure([]) is None
