"""Tests for the manual sd_notify implementation.

Validates:
  - no-op when $NOTIFY_SOCKET is unset (dev box, tests)
  - datagram sent when $NOTIFY_SOCKET points to a real unix socket
  - convenience helpers send the expected payload strings
"""
import os
import socket
from pathlib import Path

import pytest

from gsfluent.core.sdnotify import (
    notify,
    notify_ready,
    notify_status,
    notify_watchdog,
)


def test_notify_is_noop_when_env_unset(monkeypatch) -> None:
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    # Returns False (did not send); does not raise.
    assert notify("READY=1") is False


def test_notify_writes_to_unix_socket(monkeypatch, tmp_path: Path) -> None:
    sock_path = tmp_path / "notify.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(str(sock_path))
    try:
        monkeypatch.setenv("NOTIFY_SOCKET", str(sock_path))
        assert notify("READY=1") is True
        server.settimeout(2.0)
        data, _ = server.recvfrom(4096)
        assert data == b"READY=1"
    finally:
        server.close()


def test_notify_ready_sends_ready_equals_one(monkeypatch, tmp_path: Path) -> None:
    sock_path = tmp_path / "notify.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(str(sock_path))
    try:
        monkeypatch.setenv("NOTIFY_SOCKET", str(sock_path))
        notify_ready()
        server.settimeout(2.0)
        data, _ = server.recvfrom(4096)
        assert b"READY=1" in data
    finally:
        server.close()


def test_notify_watchdog_sends_watchdog_equals_one(monkeypatch, tmp_path: Path) -> None:
    sock_path = tmp_path / "notify.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(str(sock_path))
    try:
        monkeypatch.setenv("NOTIFY_SOCKET", str(sock_path))
        notify_watchdog()
        server.settimeout(2.0)
        data, _ = server.recvfrom(4096)
        assert b"WATCHDOG=1" in data
    finally:
        server.close()


def test_notify_status_sends_status_string(monkeypatch, tmp_path: Path) -> None:
    sock_path = tmp_path / "notify.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(str(sock_path))
    try:
        monkeypatch.setenv("NOTIFY_SOCKET", str(sock_path))
        notify_status("recovering 3 runs")
        server.settimeout(2.0)
        data, _ = server.recvfrom(4096)
        assert b"STATUS=recovering 3 runs" in data
    finally:
        server.close()


def test_notify_multiline_payload(monkeypatch, tmp_path: Path) -> None:
    """systemd protocol supports newline-separated key=value pairs in one datagram."""
    sock_path = tmp_path / "notify.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(str(sock_path))
    try:
        monkeypatch.setenv("NOTIFY_SOCKET", str(sock_path))
        assert notify("READY=1\nSTATUS=ok") is True
        server.settimeout(2.0)
        data, _ = server.recvfrom(4096)
        assert b"READY=1" in data
        assert b"STATUS=ok" in data
    finally:
        server.close()


def test_notify_abstract_socket(monkeypatch) -> None:
    """systemd uses abstract sockets (leading '@' in $NOTIFY_SOCKET) in
    some setups. Our implementation should handle that path too.

    Linux abstract sockets: the path starts with NUL byte; systemd encodes
    this as a leading '@' in the env var.
    """
    abstract_name = "@gsfluent-test-abstract-notify"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    # Bind abstract: prepend NUL byte to the name.
    try:
        server.bind("\0" + abstract_name[1:])
    except OSError:
        pytest.skip("abstract sockets unavailable on this platform")
    try:
        monkeypatch.setenv("NOTIFY_SOCKET", abstract_name)
        assert notify("READY=1") is True
        server.settimeout(2.0)
        data, _ = server.recvfrom(4096)
        assert data == b"READY=1"
    finally:
        server.close()


def test_notify_swallows_send_errors(monkeypatch, tmp_path: Path) -> None:
    """If $NOTIFY_SOCKET points to a non-existent path, notify() returns
    False but does not raise - the backend must keep running even if
    systemd's listener has died."""
    monkeypatch.setenv("NOTIFY_SOCKET", str(tmp_path / "does_not_exist.sock"))
    assert notify("READY=1") is False
