"""ファイル配信のヘッダ。

原本は画面内の iframe で開くので inline でなければならない。filename= を
渡すと Starlette が Content-Disposition: attachment を付け、ブラウザが
ダウンロード扱いにして iframe が真っ白になる。見た目には「壊れている」と
分からないので、ヘッダをテストで固定する。

生成した資料は逆に、保存してもらうものなので attachment のままにする。
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.responses import FileResponse  # noqa: E402


@pytest.fixture
def sample(tmp_path):
    path = tmp_path / "mysoku.jpg"
    path.write_bytes(b"\xff\xd8\xff\xe0dummy")
    return path


def _disposition(response: FileResponse) -> str:
    return response.headers.get("content-disposition", "")


class TestSourcePreview:
    def test_inline_lets_the_browser_render_it(self, sample):
        """レビュー画面の原本プレビュー。inline でないと iframe に出ない。"""
        response = FileResponse(
            sample, media_type="image/jpeg", filename=sample.name,
            content_disposition_type="inline",
        )
        assert _disposition(response).startswith("inline")
        assert sample.name in _disposition(response)
        assert response.media_type == "image/jpeg"

    def test_filename_alone_forces_attachment(self, sample):
        """content_disposition_type を省くと attachment になる。これを踏んだ。"""
        response = FileResponse(sample, media_type="image/jpeg", filename=sample.name)
        assert _disposition(response).startswith("attachment")


class TestDocumentDownload:
    def test_generated_document_is_attachment(self, sample):
        """資料は保存してもらうものなので、ダウンロードのままでよい。"""
        response = FileResponse(
            sample,
            media_type=(
                "application/vnd.openxmlformats-officedocument.presentationml.presentation"
            ),
            filename="渋谷ハイツ_mysoku.pptx",
        )
        assert _disposition(response).startswith("attachment")


def test_routes_use_the_intended_disposition():
    """実装が上の方針から外れていないことを、ソースを読んで確認する。

    ヘッダの向きは実際にブラウザで開くまで気づけないので、
    コード側の意図をここで固定しておく。
    """
    from pathlib import Path

    routes = Path(__file__).resolve().parents[1] / "app" / "api" / "routes"
    properties_source = (routes / "properties.py").read_text(encoding="utf-8")
    documents_source = (routes / "documents.py").read_text(encoding="utf-8")

    assert 'content_disposition_type="inline"' in properties_source, (
        "原本プレビューが inline になっていない"
    )
    assert "filename=" in documents_source, "資料のダウンロードにファイル名が付いていない"
