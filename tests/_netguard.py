"""Shared offline network guard for tests (test-only helper, not a test module).

block_network() patches socket.socket.connect so any connect raises AssertionError(message),
EXCEPT a connect made while socket.socketpair() is running. On Windows there is no native
socketpair: CPython uses socket._fallback_socketpair, which connects a TCP socket to its own
listener on 127.0.0.1. asyncio calls socket.socketpair() whenever it starts an event loop
(_make_self_pipe), so a plain connect guard breaks every asyncio test on Windows.
Plain loopback connects stay denied (a localhost proxy could forward to a real API).

Windows simulation on any OS (replaces socket.socketpair by CPython's real fallback):
    PYTHONPATH=tests python -m pytest -p _netguard --windows-socketpair tests/...
"""
import socket
import threading

_state = threading.local()          # .inside_socketpair: True only on the thread inside socketpair()


def simulate_windows_socketpair(monkeypatch):
    """Make socket.socketpair the CPython fallback that Windows uses. Install BEFORE block_network."""
    fallback = getattr(socket, '_fallback_socketpair', None)
    if fallback is None:
        raise RuntimeError('socket._fallback_socketpair missing on this Python; cannot simulate Windows')
    monkeypatch.setattr(socket, 'socketpair', fallback)
    return fallback


def block_network(monkeypatch, message='Network forbidden'):
    """Deny socket.socket.connect, except socketpair's own internal loopback connect."""
    original_socketpair = socket.socketpair
    original_connect = socket.socket.connect

    def socketpair(*args, **kwargs):
        previous = getattr(_state, 'inside_socketpair', False)
        _state.inside_socketpair = True
        try:
            return original_socketpair(*args, **kwargs)
        finally:
            _state.inside_socketpair = previous

    def deny(self, *args, **kwargs):
        if getattr(_state, 'inside_socketpair', False):
            return original_connect(self, *args, **kwargs)
        raise AssertionError(message)

    socketpair.wrapped = original_socketpair
    monkeypatch.setattr(socket, 'socketpair', socketpair)
    monkeypatch.setattr(socket.socket, 'connect', deny)
    return socketpair


# ---- optional pytest plugin: whole-run Windows simulation (only when loaded with -p _netguard)
_saved = []


def pytest_addoption(parser):
    parser.addoption('--windows-socketpair', action='store_true', default=False,
                     help='simulate Windows: socket.socketpair = socket._fallback_socketpair for the run')


def pytest_configure(config):
    if config.getoption('--windows-socketpair'):
        if not hasattr(socket, '_fallback_socketpair'):
            raise RuntimeError('socket._fallback_socketpair missing on this Python; cannot simulate Windows')
        _saved.append(socket.socketpair)
        socket.socketpair = socket._fallback_socketpair


def pytest_unconfigure(config):
    if _saved:
        socket.socketpair = _saved.pop()


def pytest_report_header(config):
    if config.getoption('--windows-socketpair'):
        return 'netguard: Windows socketpair simulation ON (socket._fallback_socketpair)'
