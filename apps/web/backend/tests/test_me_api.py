"""「我的」端点：自建技能（本任务）+ 已安装的内置技能。"""
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
BODY = {"name": "my-skill", "description": "自建演示", "content": "## 目标\n演示"}


def test_create_and_list_my_skill(client):
    created = client.post("/api/v1/me/skills", json=BODY, headers=HEADERS)
    assert created.status_code == 201
    rows = client.get("/api/v1/me/skills", headers=HEADERS).json()
    row = next(r for r in rows if r["name"] == "my-skill")
    assert row["source"] == "mine" and row["enabled"] is True


def test_my_skill_name_conflict_with_builtin(client):
    # "data-analysis" 是内置目录条目；自建同名必须被拒
    dup = client.post("/api/v1/me/skills", json={
        "name": "data-analysis", "description": "我", "content": "## 目标\ny"},
        headers=HEADERS)
    assert dup.status_code == 409


def test_my_skill_invalid_name_is_422_not_409(client):
    """非法 kebab-case 属 422；SkillNameTaken 才是 409（两者都是 ValueError 子类，别搞反）。"""
    bad = client.post("/api/v1/me/skills", json={
        "name": "Bad_Name", "description": "x", "content": "y"}, headers=HEADERS)
    assert bad.status_code == 422


def test_update_and_delete_only_own(client):
    client.post("/api/v1/me/skills", json=BODY, headers=HEADERS)
    updated = client.patch("/api/v1/me/skills/my-skill", json={
        "description": "改过", "content": "## 目标\n改"}, headers=HEADERS)
    assert updated.status_code == 200 and updated.json()["description"] == "改过"
    assert client.delete("/api/v1/me/skills/my-skill", headers=HEADERS).status_code == 200
    assert client.delete("/api/v1/me/skills/my-skill", headers=HEADERS).status_code == 404


def test_cannot_edit_or_delete_non_own_skill(client):
    """内置技能不在用户目录里 → 改/删都 403/404，不能动到 catalog。"""
    assert client.patch("/api/v1/me/skills/data-analysis", json={
        "description": "x", "content": "y"}, headers=HEADERS).status_code == 403
    assert client.delete("/api/v1/me/skills/data-analysis", headers=HEADERS).status_code == 404


def test_my_skills_lists_installed_builtin(client):
    """已安装的内置技能出现在「我的」里，且带 enabled 状态。"""
    client.put("/api/v1/me/capabilities/skill/data-analysis",
               json={"installed": True}, headers=HEADERS)
    rows = client.get("/api/v1/me/skills", headers=HEADERS).json()
    row = next(r for r in rows if r["name"] == "data-analysis")
    assert row["source"] == "installed" and row["builtin"] is True and row["enabled"] is True

    client.put("/api/v1/me/capabilities/skill/data-analysis",
               json={"enabled": False}, headers=HEADERS)
    rows = client.get("/api/v1/me/skills", headers=HEADERS).json()
    row = next(r for r in rows if r["name"] == "data-analysis")
    assert row["enabled"] is False          # 停用后仍在「我的」里，但状态为停用
    assert row["installed"] is True
