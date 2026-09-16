"""会话运行装配解析：把会话文档解析成"跑一轮对话"所需的全部参数。

发消息（sessions_api.send_message）与任务完成唤醒（AgentService.wake）必须
用同一份解析口径——否则两条路径会跑在不同的工作根、或挑到不同的模型。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synlys_harness import ModelProviderConfig

from app.services import workspace


class NoUsableProvider(RuntimeError):
    """会话无法解析出可用的模型服务（未指定/不存在/已停用/解密失败）。

    消息文本面向用户/管理员，API 层据此映射 422。
    """


@dataclass(frozen=True)
class SessionRuntime:
    """一轮对话的运行装配结果。

    Attributes:
        assistant: 助手文档（未选/已删为 None，走无 persona 路径）。
        provider_cfg: 已解密的模型服务配置。
        workspace_root: 工作根（绑定项目 = 项目目录；否则会话目录）。
        ownership: 文件记录归属（{"project_id": pid} 或 {"session_id": sid}）。
    """

    assistant: dict | None
    provider_cfg: ModelProviderConfig
    workspace_root: Path
    ownership: dict


async def _resolve_provider(provider_id: str | None, owner_desc: str,
                            repos: Any) -> ModelProviderConfig:
    """解析模型服务 id 为后端配置（解密 api_key）。

    Args:
        provider_id: 模型服务 id（会话级覆盖或助手绑定，调用方已按优先级取好）。
        owner_desc: 归属描述（助手/会话名，未指定 id 时的 422 提示用）。
        repos: repo 集中访问对象。

    Returns:
        ModelProviderConfig。

    Raises:
        NoUsableProvider: 未指定/不存在/已停用/解密失败。

    与 assistants_api._validate_provider 的分工：此处解密后构造出可用的
    ModelProviderConfig（助手保存时的"绑定前体检"），那边只做存在性校验、
    不接触密钥，故不合并。
    """
    if not provider_id:
        raise NoUsableProvider(f"未指定模型服务: {owner_desc}")
    try:
        decrypted = await repos.provider.get_decrypted(provider_id)
    except RuntimeError as exc:
        raise NoUsableProvider(str(exc)) from exc
    if decrypted is None:
        raise NoUsableProvider(f"模型服务不存在: {provider_id}")
    if not decrypted.get("enabled"):
        raise NoUsableProvider(
            f"模型服务已停用: {decrypted.get('name', provider_id)}")
    return ModelProviderConfig(
        name=str(decrypted.get("name", "")),
        base_url=str(decrypted.get("base_url", "")),
        api_key=str(decrypted.get("api_key", "")),
        model_id=str(decrypted.get("model_id", "")),
        multimodal=bool(decrypted.get("multimodal")),
    )


async def resolve_session_runtime(settings: Any, project_service: Any, repos: Any,
                                  doc: dict, user: dict) -> SessionRuntime:
    """解析会话的运行装配（模型优先级：会话级覆盖 > 助手绑定 > 首个启用模型）。

    副作用：会话绑定的项目已失效时清空其 project_id（幂等，仅写一次 None）。

    Args:
        settings: 应用配置（取 data_root 解析会话工作根）。
        project_service: 项目服务（绑定项目有效时取项目根）。
        repos: repo 集中访问对象。
        doc: 会话文档（调用方已校验归属）。
        user: 当前用户 payload。

    Returns:
        SessionRuntime。

    Raises:
        NoUsableProvider: 无可用模型服务。
    """
    # 助手指针失效（未选专家/专家已删）不报错，交 chat 走无 persona 路径
    assistant = await repos.assistant.get(doc.get("assistant_id") or "")
    # 模型优先级：会话级覆盖 > 助手绑定 > 第一个启用模型（回落）。回落只作用于
    # 本次运行、**不写回会话**——查看会话必须零写入（否则 updated_at 变"刚刚"，
    # 侧栏时间/排序漂移）；口径与前端 ModelPicker 的 explicitId 一致
    pid = doc.get("model_provider_id") or (assistant or {}).get("model_provider_id")
    if not pid:
        enabled = [p for p in await repos.provider.list() if p.get("enabled")]
        if enabled:
            pid = enabled[0]["_id"]
    owner = doc.get("title") or (assistant or {}).get("name") or doc.get("_id", "")
    provider_cfg = await _resolve_provider(pid, str(owner), repos)

    # 工作根解析：会话绑定的项目有效 → 项目目录；未绑定（不选工作区的新会话）或
    # 绑定已失效（项目被删）→ 会话目录 sessions/{sid} 自身为工作区（文件、产物
    # 都跟会话走，删除会话即整目录移除）。不回落"活跃项目"：未绑定会话若跟着
    # projects[0]（随改名/上传漂移）跑，上一轮的 output/ 产物会看似凭空消失。
    project = None
    if doc.get("project_id"):
        project = await project_service.get(user["sub"], str(doc["project_id"]))
    if project is not None:
        return SessionRuntime(
            assistant=assistant, provider_cfg=provider_cfg,
            workspace_root=project_service.root_for(project),
            ownership={"project_id": project["_id"]})
    workspace_root = workspace.session_root(
        settings.data_root, user["sub"], str(doc["_id"]))
    # 绑定失效（项目已删）：清掉脏 project_id，前端据此把它归入未分组区；
    # 未绑定的会话保持 None，不写回任何项目
    if doc.get("project_id"):
        await repos.session.update(doc["_id"], {"project_id": None})
    return SessionRuntime(
        assistant=assistant, provider_cfg=provider_cfg,
        workspace_root=workspace_root, ownership={"session_id": str(doc["_id"])})
