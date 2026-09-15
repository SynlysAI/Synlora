"""用户自建专家（文件为事实源 + 实例化进 assistants）。"""
import json

import pytest

from app.services.expert_service import UserExpertService


@pytest.fixture()
def svc(tmp_path, store):
    return UserExpertService(store, tmp_path)


async def test_write_and_list_own_expert(svc, tmp_path):
    expert = await svc.write("u1", dir_name="chem", name="化学助手", avatar="🧪",
                             description="演示", system_prompt="你是化学助手",
                             tool_whitelist=["python.run"])
    assert expert["_id"] == "u1:chem"
    path = tmp_path / "users" / "u1" / "experts" / "chem" / "expert.json"
    assert json.loads(path.read_text(encoding="utf-8"))["name"] == "化学助手"
    own = await svc.list_own("u1")
    assert [e["_id"] for e in own] == ["u1:chem"]
    assert await svc.list_own("u2") == []


async def test_experts_are_scoped_to_owner(svc):
    await svc.write("u1", dir_name="chem", name="化学助手", avatar="",
                    description="demo", system_prompt="p")
    assert await svc.get_own("u1", "u1:chem") is not None
    assert await svc.get_own("u2", "u1:chem") is None


async def test_delete_removes_file_and_record(svc, tmp_path, store):
    await svc.write("u1", dir_name="chem", name="化学助手", avatar="",
                    description="demo", system_prompt="p")
    await svc.ensure_instantiated("u1")
    assert await store.get("assistants", "u1:chem") is not None
    assert await svc.delete("u1", "u1:chem") is True
    assert not (tmp_path / "users" / "u1" / "experts" / "chem").exists()
    assert await store.get("assistants", "u1:chem") is None


async def test_instantiate_is_idempotent(svc, store):
    await svc.write("u1", dir_name="chem", name="化学助手", avatar="",
                    description="demo", system_prompt="p")
    await svc.ensure_instantiated("u1")
    await store.update("assistants", "u1:chem", {"name": "管理员改过"})
    await svc.ensure_instantiated("u1")  # 已存在 → 不覆盖
    assert (await store.get("assistants", "u1:chem"))["name"] == "管理员改过"
