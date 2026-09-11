"""项目 API 测试：列表/新建（重名加后缀、非法名 422）/删除/目录树 404/未认证 401。

认证夹具沿用 conftest 的 user_headers（普通用户，sub=u-user）。
"""


async def test_list_projects_returns_empty(client, user_headers):
    """全新用户列表为空（迁移无副作用，不补种默认项目）。"""
    r = await client.get("/api/v1/projects", headers=user_headers)
    assert r.status_code == 200 and r.json() == []


async def test_create_project_then_tree(client, user_headers):
    """新建项目回 201（_id/dir_name），随后 tree 列出根下三个子目录。"""
    r = await client.post("/api/v1/projects", json={"name": "实验一"}, headers=user_headers)
    assert r.status_code == 201
    pid = r.json()["_id"]
    assert r.json()["dir_name"] == "实验一"

    r2 = await client.get(f"/api/v1/projects/{pid}/tree", headers=user_headers)
    assert r2.status_code == 200
    assert {e["name"] for e in r2.json()} == {"files", "output", "tmp"}


async def test_create_duplicate_name_succeeds_with_distinct_dir(client, user_headers):
    """同名项目不失败：第二个自动拿到带后缀的目录名。"""
    await client.post("/api/v1/projects", json={"name": "x"}, headers=user_headers)
    r = await client.post("/api/v1/projects", json={"name": "x"}, headers=user_headers)
    assert r.status_code == 201 and r.json()["dir_name"] == "x-2"


async def test_create_project_rejects_empty_name(client, user_headers):
    """项目名全是空白字符时 422。"""
    r = await client.post("/api/v1/projects", json={"name": "   "}, headers=user_headers)
    assert r.status_code == 422


async def test_delete_project_removes_it(client, user_headers):
    """删除项目返回 200，之后列表为空。"""
    pid = (await client.post("/api/v1/projects", json={"name": "x"},
                             headers=user_headers)).json()["_id"]
    assert (await client.delete(f"/api/v1/projects/{pid}",
                                headers=user_headers)).status_code == 200
    assert (await client.get("/api/v1/projects", headers=user_headers)).json() == []


async def test_delete_project_twice_second_is_404(client, user_headers):
    """删除成功后再次删除同一 id 返回 404（404 只代表记录不存在，不代表目录删不掉）。"""
    pid = (await client.post("/api/v1/projects", json={"name": "x"},
                             headers=user_headers)).json()["_id"]
    assert (await client.delete(f"/api/v1/projects/{pid}",
                                headers=user_headers)).status_code == 200
    assert (await client.delete(f"/api/v1/projects/{pid}",
                                headers=user_headers)).status_code == 404


async def test_tree_missing_project_404(client, user_headers):
    """tree 对不存在的项目 id 返回 404（服务层 ValueError 映射）。"""
    r = await client.get("/api/v1/projects/no-such-project/tree", headers=user_headers)
    assert r.status_code == 404


async def test_list_projects_without_token_401(client):
    """未带认证头访问项目列表应 401。"""
    r = await client.get("/api/v1/projects")
    assert r.status_code == 401
