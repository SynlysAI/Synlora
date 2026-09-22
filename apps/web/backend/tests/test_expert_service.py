"""用户自建专家（文件为唯一事实源，不进全局 assistants 集合）。"""
import json

import pytest

from app.services.expert_service import UserExpertService


@pytest.fixture()
def svc(tmp_path):
    return UserExpertService(tmp_path)


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


async def test_write_does_not_touch_assistants(svc, store):
    """专家只写文件：assistants 集合（管理员资产）不被写入任何记录。"""
    await svc.write("u1", dir_name="chem", name="化学助手", avatar="🧪",
                    description="演示", system_prompt="你是化学助手",
                    skill_refs=["data-analysis"], mcp_refs=["lab-db"],
                    suggested_prompts=["分析这组数据"])
    assert await store.list("assistants") == []


async def test_loaded_expert_is_assistant_shaped(svc):
    """读出的专家与助手文档同构（含运行期需要的缺省字段）。"""
    await svc.write("u1", dir_name="chem", name="化学助手", avatar="🧪",
                    description="演示", system_prompt="你是化学助手")
    loaded = await svc.get_own("u1", "u1:chem")
    assert loaded is not None
    assert loaded["model_provider_id"] is None
    assert loaded["knowledge_base_ids"] == []
    assert loaded["system_prompt"] == "你是化学助手"


async def test_experts_are_scoped_to_owner(svc):
    await svc.write("u1", dir_name="chem", name="化学助手", avatar="",
                    description="demo", system_prompt="p")
    assert await svc.get_own("u1", "u1:chem") is not None
    assert await svc.get_own("u2", "u1:chem") is None


async def test_delete_removes_directory(svc, tmp_path):
    await svc.write("u1", dir_name="chem", name="化学助手", avatar="",
                    description="demo", system_prompt="p")
    assert await svc.delete("u1", "u1:chem") is True
    assert not (tmp_path / "users" / "u1" / "experts" / "chem").exists()
    assert await svc.delete("u1", "u1:chem") is False


async def test_expert_id_rejects_path_traversal(svc, tmp_path):
    """expert_id 的目录名切片来自调用方，越界片段不得落到别人的专家文件上。"""
    await svc.write("u2", dir_name="chem", name="化学助手", avatar="",
                    description="demo", system_prompt="p")
    for bad in ("u1:../../u2/experts/chem", "u1:..\\..\\u2\\experts\\chem",
                "u1:", "u1:.", "u1:.."):
        assert await svc.get_own("u1", bad) is None
        assert await svc.delete("u1", bad) is False
    # 越界删除被拒后，别人的专家文件原样保留
    assert (tmp_path / "users" / "u2" / "experts" / "chem" / "expert.json").is_file()


async def test_purge_legacy_records_only_removes_owner_docs(store):
    """启动清理只删带 owner 的历史实例记录，管理员资产原样保留。"""
    await store.insert("assistants", {"_id": "u1:chem", "name": "化学助手",
                                      "owner": "u1", "builtin": False})
    await store.insert("assistants", {"_id": "asst-research", "name": "科研助手",
                                      "builtin": True})
    removed = await UserExpertService.purge_legacy_records(store)
    assert removed == 1
    assert await store.get("assistants", "u1:chem") is None
    assert await store.get("assistants", "asst-research") is not None
