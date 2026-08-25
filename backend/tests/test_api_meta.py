"""ルートとメタ情報のエンドポイント。

デプロイ直後にブラウザで開かれるのはルートなので、ここが 404 だと
動いているのに「動いていない」と誤解される。
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def test_root_is_not_404(client):
    response = client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    # 操作画面が別サービスであることが読み取れること
    assert "API" in body["note"]


def test_root_lists_reachable_endpoints(client):
    """案内に載せたパスが実在すること。

    リンク切れの案内は無いより悪い。
    """
    endpoints = client.get("/").json()["endpoints"]
    # app.routes は include_router した分がネストして入るため、
    # 実際に公開されているパスは OpenAPI から引く。
    published = set(app.openapi()["paths"])
    for name, path in endpoints.items():
        if path == "/docs":
            continue  # Swagger UI は OpenAPI に載らない
        assert path in published, f"案内に載せた {name}={path} が存在しない"


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_config_exposes_no_secrets(client):
    """UI が起動時に読む設定。認証情報を混ぜないこと。"""
    body = client.get("/config").json()
    serialized = str(body).lower()
    for forbidden in ("api_key", "sk-ant", "password", "secret", "token"):
        assert forbidden not in serialized, f"/config に {forbidden} が露出している"


def test_field_specs_come_from_definition_file(client):
    """UI のラベルは項目定義ファイルから引く。TypeScript 側に書き写さない。"""
    specs = client.get("/properties/field-specs").json()
    assert len(specs) > 0
    keys = {spec["key"] for spec in specs}
    assert {"property_name", "address", "price"} <= keys
    assert all(spec["label"] for spec in specs)
