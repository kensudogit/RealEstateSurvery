"""ジョブ投入の失敗時の振る舞い。

ブローカーへ繋がらないとき、既定の Celery は再試行を重ねて 60 秒近く
返ってこない。UI から見ると「実行ボタンを押したまま固まる」という
一番困る壊れ方になるので、押した直後にエラーを返せることを確認する。
"""

from __future__ import annotations

import socket

import pytest

from app.api.deps import broker_reachable


class TestBrokerReachable:
    def test_reachable_when_listening(self):
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        try:
            assert broker_reachable(f"redis://127.0.0.1:{port}/0") is True
        finally:
            server.close()

    def test_not_reachable_when_closed(self):
        # 未使用ポートを確保してすぐ閉じる
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        assert broker_reachable(f"redis://127.0.0.1:{port}/0", timeout=0.5) is False

    def test_unresolvable_host_fails_fast(self):
        """名前解決の失敗が一番待たされるパターン。ここで止める。"""
        assert broker_reachable(
            "redis://this-host-does-not-exist.invalid:6379/0", timeout=1.0
        ) is False

    @pytest.mark.parametrize(
        "url,expected_port",
        [
            ("redis://host/0", 6379),
            ("rediss://host/0", 6380),   # TLS の既定ポート
            ("redis://host:6380/0", 6380),
        ],
    )
    def test_default_ports(self, url, expected_port, monkeypatch):
        seen = {}

        def fake_connect(address, timeout=None):
            seen["address"] = address
            raise OSError("接続しない")

        monkeypatch.setattr(socket, "create_connection", fake_connect)
        broker_reachable(url)
        assert seen["address"][1] == expected_port

    def test_malformed_url_is_not_reachable(self):
        assert broker_reachable("") is False
        assert broker_reachable("not-a-url") is False
