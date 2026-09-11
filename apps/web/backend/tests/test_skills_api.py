"""技能 API 测试：列表（含内置）/增改删/导入导出/权限与路径穿越护栏。

技能是全局资源（admin 管理、所有登录用户可读），路由以技能名为 key。
认证夹具沿用 conftest 的 user_headers（普通用户，sub=u-user）与
admin_headers（管理员，sub=u-admin）。
"""


async def test_list_skills_includes_seeds(client, user_headers):
    """普通用户也能读到随包内置的技能。"""
    names = {s["name"] for s in (await client.get("/api/v1/skills", headers=user_headers)).json()}
    assert {"data-analysis", "pdf-extraction"} <= names


async def test_create_skill_requires_admin(client, user_headers):
    """普通用户新建技能 403（写操作需管理员）。"""
    r = await client.post("/api/v1/skills",
                          json={"name": "my-skill", "description": "d", "content": "# 目标"},
                          headers=user_headers)
    assert r.status_code == 403


async def test_admin_creates_then_reads_export(client, admin_headers):
    """管理员新建后可按名导出 SKILL.md 原文（frontmatter + 正文）。"""
    r = await client.post("/api/v1/skills",
                          json={"name": "my-skill", "description": "用途", "content": "# 目标\n做点事"},
                          headers=admin_headers)
    assert r.status_code == 201
    exported = await client.get("/api/v1/skills/my-skill/export", headers=admin_headers)
    assert exported.status_code == 200
    assert "name: my-skill" in exported.text and "# 目标" in exported.text


async def test_import_skill_md(client, admin_headers):
    """导入 SKILL.md 全文后技能名取自 frontmatter。"""
    r = await client.post("/api/v1/skills/import",
                          json={"text": "---\nname: imported\ndescription: 导入的\n---\n# 目标\n正文"},
                          headers=admin_headers)
    assert r.status_code == 201 and r.json()["name"] == "imported"


async def test_import_bad_text_422(client, admin_headers):
    """缺少 frontmatter 的文本导入 422。"""
    r = await client.post("/api/v1/skills/import", json={"text": "没有 frontmatter"},
                          headers=admin_headers)
    assert r.status_code == 422


async def test_delete_builtin_conflict(client, admin_headers):
    """内置技能不可删除，返回 409。"""
    assert (await client.delete("/api/v1/skills/data-analysis",
                                headers=admin_headers)).status_code == 409


async def test_list_skills_without_token_401(client):
    """未带认证头访问技能列表应 401。"""
    assert (await client.get("/api/v1/skills")).status_code == 401


async def test_patch_skill_overwrites(client, admin_headers):
    """PATCH 覆盖写：以路径里的技能名为准，描述与正文都被更新。"""
    await client.post("/api/v1/skills",
                      json={"name": "patch-me", "description": "旧", "content": "# 旧正文"},
                      headers=admin_headers)
    r = await client.patch("/api/v1/skills/patch-me",
                           json={"name": "ignored", "description": "新用途", "content": "# 新正文"},
                           headers=admin_headers)
    assert r.status_code == 200
    skills = {s["name"]: s for s in
              (await client.get("/api/v1/skills", headers=admin_headers)).json()}
    assert skills["patch-me"]["description"] == "新用途"
    assert skills["patch-me"]["content"] == "# 新正文"


async def test_patch_skill_without_name_in_body(client, admin_headers):
    """PATCH 的请求体不需要 name（技能名以路径参数为准）。"""
    await client.post("/api/v1/skills",
                      json={"name": "no-name-body", "description": "旧", "content": "# 旧"},
                      headers=admin_headers)
    r = await client.patch("/api/v1/skills/no-name-body",
                           json={"description": "新", "content": "# 新"},
                           headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["name"] == "no-name-body" and r.json()["description"] == "新"


async def test_delete_custom_skill_removes_it(client, admin_headers):
    """删除自建技能 200，之后列表里不再出现。"""
    await client.post("/api/v1/skills",
                      json={"name": "temp-skill", "description": "d", "content": "# x"},
                      headers=admin_headers)
    assert (await client.delete("/api/v1/skills/temp-skill",
                                headers=admin_headers)).status_code == 200
    names = {s["name"] for s in
             (await client.get("/api/v1/skills", headers=admin_headers)).json()}
    assert "temp-skill" not in names


async def test_create_illegal_skill_name_422(client, admin_headers):
    """POST 建技能传穿越名（含 Windows 反斜杠）一律 422，技能根外无残留。"""
    for bad in ("../evil", "..\\..\\evil", "Bad_Name", "-lead", "a b"):
        r = await client.post("/api/v1/skills",
                              json={"name": bad, "description": "d", "content": "# x"},
                              headers=admin_headers)
        assert r.status_code == 422, bad


async def test_delete_illegal_skill_name_cannot_escape(client, admin_headers, app):
    """DELETE 穿越名不得越出技能根：反斜杠串真的到达路由，被服务层护栏挡下。

    技能根外放一个 canary 文件兜底。两种穿越写法都试：

    - `..%5C..%5Cevil`（解码为 `..\\..\\evil`）：Windows 上 `\\` 是路径分隔符，
      这个串**确实**会作为 `{name}` 到达路由（单段、Starlette 的 `[^/]+` 放行），
      由 SkillService 的 kebab-case 校验拒绝 → 404，canary 必须原样；
    - `..%2F..%2Fevil`（解码为 `../../evil`）：`/` 不可能落在单段路径参数里，
      Starlette 路由不到本 API（落到 SPA 静态挂载 → 405），同样碰不到磁盘。
    """
    data_root = app.state.settings.data_root
    canary = data_root / "canary.txt"
    canary.write_text("keep", encoding="utf-8")

    assert (await client.delete("/api/v1/skills/..%5C..%5Ccanary",
                                headers=admin_headers)).status_code in (404, 422)
    assert canary.read_text(encoding="utf-8") == "keep"

    assert (await client.delete("/api/v1/skills/..%2F..%2Fcanary",
                                headers=admin_headers)).status_code in (404, 405, 422)
    assert canary.read_text(encoding="utf-8") == "keep"

    assert (await client.delete("/api/v1/skills/Bad_Name",
                                headers=admin_headers)).status_code == 404
