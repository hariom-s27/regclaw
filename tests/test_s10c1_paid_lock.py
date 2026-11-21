"""OS process locking on invented roots; no real signals or service calls."""
import builtins
from contextlib import contextmanager
import io
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

import pytest

import _netguard
from eval.s10 import frozen_candidates as FC
from eval.s10 import live as L
from eval.s10.paid_lock import paid_lock, PaidLockHeld
from llm import provider


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path_factory):
    monkeypatch.setenv('REGCLAW_STUB', '1')
    monkeypatch.setenv('LANGFUSE_ENABLED', 'false')
    _netguard.block_network(monkeypatch, 'paid lock tests forbid network')
    repo = Path(__file__).resolve().parents[1]
    base = tmp_path_factory.getbasetemp().resolve()

    def guarded(real):
        def opened(file, mode='r', *args, **kwargs):
            if isinstance(file, (str, os.PathLike)):
                path = Path(file).resolve()
                assert not any(part.casefold().startswith('.env') or any(
                    name in part.casefold() for name in ('holdout', 'jid_map', 'profiles_draft')) for part in path.parts)
                if not path.is_relative_to(base):
                    assert not path.is_relative_to(repo / 'data')
                    assert not path.is_relative_to(repo / 'standards/data')
                    assert not any(char in mode for char in 'wax+')
            return real(file, mode, *args, **kwargs)
        return opened

    monkeypatch.setattr(builtins, 'open', guarded(builtins.open))
    monkeypatch.setattr(io, 'open', guarded(io.open))
    yield
    assert os.environ['REGCLAW_STUB'] == '1'


@pytest.fixture
def root(tmp_path_factory):
    path = tmp_path_factory.mktemp('paidlockworld')
    assert 's8' not in str(path).casefold()
    return path


CHILD = """
import builtins, io, os, queue, socket, sys, threading
from pathlib import Path
root = Path(sys.argv[1]).resolve()
repo = Path.cwd().resolve()
assert 's8' not in str(root).casefold()
def denied(*args, **kwargs):
    raise AssertionError('child network forbidden')
socket.create_connection = denied
socket.socket.connect = denied
socket.socket.connect_ex = denied
def guarded(real):
    def opened(file, mode='r', *args, **kwargs):
        if isinstance(file, (str, os.PathLike)):
            path = Path(file).resolve()
            assert not any(p.casefold().startswith('.env') or any(
                name in p.casefold() for name in ('holdout', 'jid_map', 'profiles_draft')) for p in path.parts)
            if not path.is_relative_to(root):
                assert not path.is_relative_to(repo / 'data')
                assert not path.is_relative_to(repo / 'standards/data')
                assert not any(c in mode for c in 'wax+')
        return real(file, mode, *args, **kwargs)
    return opened
builtins.open = guarded(builtins.open)
io.open = guarded(io.open)
from eval.s10.paid_lock import paid_lock
with paid_lock(root):
    print('READY', flush=True)
    release = queue.Queue()
    threading.Thread(target=lambda: release.put(sys.stdin.readline()), daemon=True).start()
    release.get(timeout=30)
"""


@contextmanager
def holding_child(root):
    child = subprocess.Popen([sys.executable, '-B', '-c', CHILD, str(root)],
                             cwd=Path(__file__).resolve().parents[1], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    ready = queue.Queue()
    reader = threading.Thread(target=lambda: ready.put(child.stdout.readline()), daemon=True)
    reader.start()
    try:
        assert ready.get(timeout=30) == 'READY\n'
        yield child
    finally:
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=30)
        reader.join(timeout=30)
        assert not reader.is_alive()
        for stream in (child.stdin, child.stdout, child.stderr):
            stream.close()


def test_paid_lock_contender_refuses_before_ledger_access(root, monkeypatch):
    with holding_child(root):
        before = time.monotonic()
        with pytest.raises(PaidLockHeld, match='another paid S10c process is running'):
            with paid_lock(root):
                pytest.fail('contender acquired lock')
        assert time.monotonic() - before < 5

        def no_ledger(*args, **kwargs):
            pytest.fail('ledger accessed before lock acquisition')

        monkeypatch.setattr(FC, 'read_call_ledger', no_ledger)
        monkeypatch.setattr(FC, 'append_ledger_line', no_ledger)
        monkeypatch.setattr(provider, 'ROOT', root)
        lines = []
        args = ['pilot', '--profiles', 'P.yaml', '--candidates', 'C.json', '--candidates-manifest', 'CM.json',
                '--candidates-run-manifest', 'CR.json', '--out-dir', 'pilot']
        assert L.main(args, root=root, out=lines.append) == 2
        assert lines == ['another paid S10c process is running']
        assert FC.main(['build']) == 2
        assert not list(root.rglob('*.jsonl'))
        assert root / 'data/runs/s10c_paid.lock' == next(root.rglob('*.lock'))
    # The first child was terminated while holding the lock.
    with paid_lock(root):
        assert (root / 'data/runs/s10c_paid.lock').is_file()


def test_paid_lock_released_after_process_exit(root):
    with holding_child(root) as child:
        child.stdin.write('finish\n')
        child.stdin.flush()
        assert child.wait(timeout=30) == 0
    lock_path = root / 'data/runs/s10c_paid.lock'
    assert lock_path.is_file()
    with paid_lock(root) as acquired:
        assert acquired == lock_path
    assert lock_path.is_file()


def test_paid_lock_released_after_exception(root):
    with pytest.raises(RuntimeError):
        with paid_lock(root):
            raise RuntimeError('FAKE failure')
    with paid_lock(root):
        assert (root / 'data/runs/s10c_paid.lock').is_file()
