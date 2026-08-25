"""Google の認証情報の解決。

マネージド環境では認証情報をファイルとして置けない。同じ設定名で
「ファイルパス」と「JSON の中身」の両方を受け取れることを固定する。
ここが崩れると、ローカルは動くのにデプロイ先だけ落ちる。
"""

from __future__ import annotations

import json

import pytest

from app.services.google_auth import GoogleAuthError, _as_json


class TestAsJson:
    def test_reads_a_file_path(self, tmp_path):
        path = tmp_path / "sa.json"
        path.write_text(json.dumps({"type": "service_account"}), encoding="utf-8")
        assert _as_json(path) == {"type": "service_account"}

    def test_reads_json_content_directly(self):
        """Railway などでは変数に中身を貼るしかない。"""
        assert _as_json('{"type": "service_account"}') == {"type": "service_account"}

    def test_json_with_surrounding_whitespace(self):
        """コピペで前後に改行が入りやすい。"""
        assert _as_json('  \n {"a": 1} \n ') == {"a": 1}

    def test_missing_path_is_none(self):
        """設定されているがファイルが無い場合。次の候補へ進めるよう None。"""
        assert _as_json("/does/not/exist.json") is None

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_unset_is_none(self, value):
        assert _as_json(value) is None

    def test_broken_json_is_reported(self):
        """途中で切れた貼り付けは黙って無視せず、原因を伝える。"""
        with pytest.raises(GoogleAuthError) as raised:
            _as_json('{"type": "service_acc')
        assert "解釈できません" in str(raised.value)


class TestLoadCredentials:
    def test_no_credentials_explains_both_paths(self, monkeypatch):
        """個人 Gmail とワークスペースで手順が違うので、両方を示す。"""
        from app.core.config import Settings, get_settings
        from app.services import google_auth

        blank = Settings(
            google_service_account_json=None,
            google_oauth_token_json=None,
            google_oauth_token_path=None,
        )
        monkeypatch.setattr(google_auth, "get_settings", lambda: blank)

        with pytest.raises(GoogleAuthError) as raised:
            google_auth.load_credentials(["scope"])

        message = str(raised.value)
        assert "GOOGLE_OAUTH_TOKEN_JSON" in message
        assert "GOOGLE_SERVICE_ACCOUNT_JSON" in message
        get_settings.cache_clear()
