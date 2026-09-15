"""能力开关端点（用户侧）：安装/卸载/启用/停用。"""
import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DEV_AUTH_TOKEN", "devtok")
    app = create_app()
    with TestClient(app) as c:
        yield c


HEADERS = {"Authorization": "Bearer devtok"}
# 用真实的内置目录条目（catalog/skills/data-analysis）：
# 公共层技能不在 catalog 里，can_install 会 404，不能拿它当被安装对象
ITEM = "data-analysis"


def test_switch_enabled_without_install_is_422(client):
    resp = client.put(f"/api/v1/me/capabilities/skill/{ITEM}",
                      json={"enabled": True}, headers=HEADERS)
    assert resp.status_code == 422


def test_switch_unknown_kind_is_404(client):
    resp = client.put(f"/api/v1/me/capabilities/nope/{ITEM}",
                      json={"installed": True}, headers=HEADERS)
    assert resp.status_code == 404


def test_switch_install_then_disable(client):
    install = client.put(f"/api/v1/me/capabilities/skill/{ITEM}",
                         json={"installed": True}, headers=HEADERS)
    assert install.status_code == 200
    assert install.json() == {"kind": "skill", "id": ITEM,
                             "installed": True, "enabled": True}

    off = client.put(f"/api/v1/me/capabilities/skill/{ITEM}",
                     json={"enabled": False}, headers=HEADERS)
    assert off.status_code == 200
    assert off.json() == {"kind": "skill", "id": ITEM,
                          "installed": True, "enabled": False}

    # 停用后从可见性中被剔除（此任务用既有 /catalog 端点校验；/market 在 Task 9 提供）
    rows = client.get("/api/v1/catalog?kind=skill", headers=HEADERS).json()
    row = next(r for r in rows if r["id"] == ITEM)
    assert row["installed"] is True
    assert row["enabled"] is False
    assert row["visible"] is False


def test_switch_uninstall_ignores_enabled_field(client):
    """卸载与 enabled 同时给出时，enabled 视为无效而不是报 422（自洽请求）。"""
    client.put(f"/api/v1/me/capabilities/skill/{ITEM}",
               json={"installed": True}, headers=HEADERS)
    resp = client.put(f"/api/v1/me/capabilities/skill/{ITEM}",
                      json={"installed": False, "enabled": False}, headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json()["installed"] is False


def test_switch_install_unknown_item_is_404(client):
    resp = client.put("/api/v1/me/capabilities/skill/no-such-skill",
                      json={"installed": True}, headers=HEADERS)
    assert resp.status_code == 404
