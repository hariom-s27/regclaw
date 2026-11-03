"""Guard tests for tests/_netguard.py: only socketpair's internal loopback connect is allowed."""
import asyncio
import gc
import socket
import threading

import pytest

import _netguard


@pytest.fixture
def windows_socketpair(monkeypatch):
    """Simulate Windows: socket.socketpair is CPython's real _fallback_socketpair (no fake)."""
    return _netguard.simulate_windows_socketpair(monkeypatch)


@pytest.fixture(autouse=True)
def reset_flag():
    yield
    _netguard._state.inside_socketpair = False


@pytest.fixture
def listener():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(('127.0.0.1', 0))
    srv.listen()
    yield srv.getsockname()[1]
    srv.close()


def _connect(addr):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(1)
        s.connect(addr)
    finally:
        s.close()


def test_a_public_address_is_denied(monkeypatch):
    _netguard.block_network(monkeypatch, 'Network forbidden')
    with pytest.raises(AssertionError, match='Network forbidden'):
        _connect(('203.0.113.1', 443))


def test_b_plain_loopback_connect_outside_socketpair_is_denied(monkeypatch, listener):
    _netguard.block_network(monkeypatch, 'Network forbidden')
    with pytest.raises(AssertionError, match='Network forbidden'):
        _connect(('127.0.0.1', listener))


def test_c_event_loop_starts_under_guard_and_windows_simulation(monkeypatch, windows_socketpair):
    wrapper = _netguard.block_network(monkeypatch, 'Network forbidden')     # simulation installed FIRST
    assert socket.socketpair is wrapper
    assert wrapper.wrapped is socket._fallback_socketpair is windows_socketpair
    assert asyncio.run(asyncio.sleep(0, result='ok')) == 'ok'
    a, b = socket.socketpair()                                               # real fallback pair works
    a.sendall(b'x')
    assert b.recv(1) == b'x'
    a.close()
    b.close()


@pytest.mark.filterwarnings('ignore::pytest.PytestUnraisableExceptionWarning')   # half-built loop's __del__
def test_c_control_naive_guard_fails_under_windows_simulation(monkeypatch, windows_socketpair):
    """The old guard (connect denied with no exception) breaks asyncio under the simulation: test (c) is not vacuous."""
    def deny(*args, **kwargs):
        raise AssertionError('Network forbidden')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    with pytest.raises(AssertionError, match='Network forbidden'):
        asyncio.new_event_loop()                   # _make_self_pipe -> socket.socketpair() -> connect
    gc.collect()


def test_d_flag_cleared_when_socketpair_raises_and_nested_calls_restore(monkeypatch, listener):
    def boom(*args, **kwargs):
        raise OSError('socketpair failed')
    monkeypatch.setattr(socket, 'socketpair', boom)
    _netguard.block_network(monkeypatch, 'Network forbidden')
    with pytest.raises(OSError, match='socketpair failed'):
        socket.socketpair()
    assert _netguard._state.inside_socketpair is False
    with pytest.raises(AssertionError, match='Network forbidden'):
        _connect(('127.0.0.1', listener))
    # nested call through the wrapper: inner restores the PREVIOUS value (True), outer restores False
    seen = []

    def nested(*args, **kwargs):
        seen.append(_netguard._state.inside_socketpair)
        if len(seen) == 1:
            socket.socketpair()
            seen.append(_netguard._state.inside_socketpair)
            raise OSError('outer socketpair failed')
    monkeypatch.setattr(socket, 'socketpair', nested)
    _netguard.block_network(monkeypatch, 'Network forbidden')
    with pytest.raises(OSError, match='outer'):
        socket.socketpair()
    assert seen == [True, True, True] and _netguard._state.inside_socketpair is False


def test_e_connect_after_successful_socketpair_is_denied(monkeypatch, windows_socketpair, listener):
    _netguard.block_network(monkeypatch, 'Network forbidden')
    a, b = socket.socketpair()
    a.close()
    b.close()
    assert _netguard._state.inside_socketpair is False
    with pytest.raises(AssertionError, match='Network forbidden'):
        _connect(('127.0.0.1', listener))


def test_flag_is_thread_local(monkeypatch, listener):
    """A connect from another thread while this thread is inside socketpair is still denied."""
    _netguard.block_network(monkeypatch, 'Network forbidden')
    errors = []
    _netguard._state.inside_socketpair = True                                # this thread only
    t = threading.Thread(target=lambda: errors.append(pytest.raises(AssertionError, _connect,
                                                                     ('127.0.0.1', listener))))
    t.start()
    t.join()
    assert len(errors) == 1 and 'Network forbidden' in str(errors[0].value)


def test_guard_patches_exactly_socket_connect(monkeypatch):
    before = {n: getattr(socket.socket, n) for n in ('connect', 'connect_ex')}
    before_cc = socket.create_connection
    _netguard.block_network(monkeypatch, 'Network forbidden')
    assert socket.socket.connect is not before['connect']
    assert socket.socket.connect_ex is before['connect_ex'] and socket.create_connection is before_cc
