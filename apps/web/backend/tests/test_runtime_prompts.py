"""宿主产品提示词与联网搜索适配测试。"""

import httpx

from synlys_harness import ToolContext

from app.runtime.prompts import build_system_prompt
from app.tools.web_search import web_search


def test_product_prompt_keeps_sections_and_order(tmp_path):
    """产品原文、动态段和专家人设保持既有顺序。"""
    output = build_system_prompt(
        persona="你是高分子专家。",
        workspace=tmp_path,
        skills=[("data-analysis", "分析数据")],
        sandbox="docker",
        shell_available=True,
        today="2026-09-17",
    )

    assert output.index("# 灵魂") < output.index("# 工作方式")
    assert output.index("# 技能") < output.index("# 输出规范")
    assert output.index("# 输出规范") < output.index("# 执行环境")
    assert output.index("# 执行环境") < output.index("# 工作区")
    assert output.rstrip().endswith("你是高分子专家。")
    assert tmp_path.as_posix() in output
    assert "`data-analysis`：分析数据" in output
    assert "shell.run" in output


async def test_web_search_keeps_searxng_result_format(tmp_path, monkeypatch):
    """宿主搜索适配保持原工具名、请求参数和结果格式。"""
    async def fake_get(*_args, **_kwargs):
        return httpx.Response(
            200,
            request=httpx.Request("GET", "http://sx.test/search"),
            json={
                "answers": ["即时答案"],
                "results": [{
                    "title": "论文标题",
                    "url": "https://example.test/paper",
                    "content": "论文摘要",
                }],
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    ctx = ToolContext(
        user_id="u1",
        run_id="r1",
        workspace_root=tmp_path,
        extra={"web_search_endpoint": "http://sx.test", "web_search_api_key": "token"},
    )

    result = await web_search(ctx, {"query": "polymer", "max_results": 3})

    assert result.ok
    assert "即时答案" in result.content
    assert "论文标题" in result.content
    assert result.data["results"] == 1
