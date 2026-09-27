"""catalog MCP 条目扫描测试。"""
import json

from app.catalog.loader import scan_catalog


def _write(root, mcp_id, data):
    d = root / "mcp" / mcp_id
    d.mkdir(parents=True)
    (d / "mcp.json").write_text(json.dumps(data), encoding="utf-8")


HTTP_OK = {
    "id": "remote-api", "name": "远程 API", "description": "d",
    "transport": "streamable-http", "url": "https://example.com/mcp",
}
STDIO_OK = {
    "id": "fs", "name": "文件系统", "description": "d",
    "transport": "stdio", "command": "npx", "args": ["-y", "x"],
}


def test_scan_mcps_ok(tmp_path):
    _write(tmp_path, "remote-api", HTTP_OK)
    _write(tmp_path, "fs", STDIO_OK)
    index = scan_catalog([tmp_path])
    assert set(index.mcps) == {"remote-api", "fs"}
    assert index.mcps["fs"].command == "npx"
    assert index.mcps["remote-api"].headers == {}


def test_scan_mcps_invalid_entries_skipped(tmp_path):
    # transport 未知 / stdio 缺 command / http url 非 http(s) / id 非 kebab
    _write(tmp_path, "bad-transport", {**HTTP_OK, "id": "bad-transport", "transport": "sse"})
    _write(tmp_path, "no-command", {**STDIO_OK, "id": "no-command", "command": ""})
    _write(tmp_path, "bad-url", {**HTTP_OK, "id": "bad-url", "url": "ftp://x"})
    _write(tmp_path, "Bad_Id", {**HTTP_OK, "id": "Bad_Id"})
    index = scan_catalog([tmp_path])
    assert index.mcps == {}


def test_scan_mcps_data_root_overrides_repo_root(tmp_path):
    repo, data = tmp_path / "repo", tmp_path / "data"
    _write(repo, "dup", {**HTTP_OK, "id": "dup", "name": "仓库版"})
    _write(data, "dup", {**HTTP_OK, "id": "dup", "name": "数据目录版"})
    index = scan_catalog([repo, data])
    assert index.mcps["dup"].name == "数据目录版"


def test_items_kinds_and_list_items(tmp_path):
    from app.catalog.items import KINDS, CatalogService
    _write(tmp_path, "remote-api", HTTP_OK)
    service = CatalogService(scan_catalog([tmp_path]))
    assert "mcp" in KINDS
    items = service.list_items("mcp")
    assert [i.id for i in items] == ["remote-api"]
    assert items[0].kind == "mcp"
