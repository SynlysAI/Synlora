"""AI⁴MS provider 插件层与共享工具注册表测试。"""
from __future__ import annotations

from synlys_harness import ToolContext, ToolRegistry, ToolResult, tool

from app.core.settings import Settings
from app.integrations import enabled_provider_names, register_providers


class _FakeProvider:
    """最小 provider：注册一个 fake.ping 工具（测试插件 seam 用）。"""

    name = "fake"

    def register(self, registry: ToolRegistry) -> None:
        """注册 fake.ping。"""
        registry.register(fake_ping)


@tool(name="fake.ping", description="测试用工具",
      parameters={"type": "object", "properties": {}})
async def fake_ping(ctx: ToolContext, args: dict) -> ToolResult:
    """测试用工具：回 pong。"""
    return ToolResult(ok=True, content="pong")


def test_enabled_provider_names_parsing():
    """启用名单按逗号拆分、去空白；未配置为空列表（默认全关）。"""
    assert enabled_provider_names(Settings(ai4ms_providers="")) == []
    assert enabled_provider_names(Settings(ai4ms_providers="spec_agent")) == ["spec_agent"]
    assert enabled_provider_names(Settings(ai4ms_providers=" spec_agent , fake ")) == [
        "spec_agent", "fake"]
    assert enabled_provider_names(Settings(ai4ms_providers="spec_agent,spec_agent")) == ["spec_agent"]


def test_register_providers_disabled_by_default():
    """默认（未配置）零注册。"""
    registry = ToolRegistry()
    assert register_providers(registry, Settings(ai4ms_providers="")) == []
    assert registry.names == []


def test_register_providers_registers_enabled(monkeypatch):
    """启用后按 provider 表注册工具。"""
    monkeypatch.setattr("app.integrations._provider_table",
                        lambda: {"fake": _FakeProvider()})
    registry = ToolRegistry()
    assert register_providers(registry, Settings(ai4ms_providers="fake")) == ["fake"]
    assert registry.names == ["fake.ping"]


def test_register_providers_unknown_name_skipped(monkeypatch):
    """未知 provider 名跳过且不抛异常（配置写错不阻断服务启动）。"""
    monkeypatch.setattr("app.integrations._provider_table", lambda: {})
    registry = ToolRegistry()
    assert register_providers(registry, Settings(ai4ms_providers="nope")) == []
    assert registry.names == []
