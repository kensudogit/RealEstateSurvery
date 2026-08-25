"""ジョブキューの接続先の組み立て。

マネージド環境の参照変数は空文字に解決されることがある。Railway では
内部ドメインが空になる事象が報告されており、REDIS_URL を設定したのに
空、という状態が起きる。そのときに個別の変数から組み立てられること、
そして「未設定」と「接続できない」を取り違えないことを確認する。
"""

from __future__ import annotations

from app.core.config import Settings


def make(**overrides) -> Settings:
    # 既定値が効かないよう、明示的に空を渡してから上書きする
    base = {"redis_url": "", "redishost": "", "redisuser": "", "redispassword": ""}
    return Settings(**{**base, **overrides})


class TestBrokerUrl:
    def test_redis_url_wins(self):
        settings = make(redis_url="redis://queue.internal:6379/2", redishost="ignored")
        assert settings.broker_url == "redis://queue.internal:6379/2"

    def test_blank_redis_url_falls_back_to_parts(self):
        """参照変数が空に解決されたケース。個別の変数で救う。"""
        settings = make(redis_url="", redishost="redis.railway.internal",
                        redisport=6379, redispassword="secret")
        assert settings.broker_url == "redis://default:secret@redis.railway.internal:6379/0"

    def test_whitespace_is_treated_as_unset(self):
        """参照変数が空白だけに解決されることもある。"""
        settings = make(redis_url="   ", redishost="redis.internal")
        assert settings.broker_url.startswith("redis://redis.internal")

    def test_user_is_honoured(self):
        settings = make(redis_url="", redishost="h", redisuser="app", redispassword="pw")
        assert settings.broker_url == "redis://app:pw@h:6379/0"

    def test_no_password_means_no_credentials(self):
        settings = make(redis_url="", redishost="h")
        assert settings.broker_url == "redis://h:6379/0"

    def test_custom_port(self):
        settings = make(redis_url="", redishost="h", redisport=6380)
        assert settings.broker_url == "redis://h:6380/0"

    def test_nothing_set_is_empty(self):
        """空を返すことで、呼び出し側が「未設定」と「接続不可」を
        区別できる。ここを既定値で埋めると原因を見失う。"""
        assert make().broker_url == ""


class TestFailureMessage:
    def test_unset_says_so(self):
        """未設定のときに「接続できません」と出すと、動いている Redis を
        疑って時間を溶かす。原因を名指しする。"""
        from app.api.deps import failure_detail

        detail = failure_detail("")
        assert "設定されていません" in detail
        assert "REDISHOST" in detail  # 回避策も示す

    def test_unreachable_shows_target_without_credentials(self):
        from app.api.deps import failure_detail

        detail = failure_detail("redis://user:secret@queue.internal:6379/0")
        assert "queue.internal:6379/0" in detail
        assert "secret" not in detail
        assert "user" not in detail
