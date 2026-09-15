"""项目 API 测试：列表/新建（重名加后缀、非法名 422）/删除/目录树 404/未认证 401。

认证夹具沿用 conftest 的 user_headers（普通用户，sub=u-user）。
"""


async def test_list_projects_returns_empty(client, user_headers):
    """全新用户列表为空（只读列表不落盘、不建目录）。"""
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


async def test_create_duplicate_name_409(client, user_headers):
    """显示名唯一：建同名工作区直接拒绝（目录名加后缀只作兜底，不再用于消解重名）。"""
    await client.post("/api/v1/projects", json={"name": "x"}, headers=user_headers)
    r = await client.post("/api/v1/projects", json={"name": "x"}, headers=user_headers)
    assert r.status_code == 409
    assert "同名" in r.json()["detail"]


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


async def test_rename_project_moves_dir(client, user_headers):
    """重命名：显示名与磁盘目录名同步改，旧目录不再存在。"""
    pid = (await client.post("/api/v1/projects", json={"name": "旧名"},
                             headers=user_headers)).json()["_id"]
    r = await client.patch(f"/api/v1/projects/{pid}", json={"name": "新名"},
                           headers=user_headers)
    assert r.status_code == 200
    assert r.json()["name"] == "新名" and r.json()["dir_name"] == "新名"

    listing = (await client.get("/api/v1/projects", headers=user_headers)).json()
    assert [p["name"] for p in listing] == ["新名"]
    # 目录树仍可用（root_for 走的是新目录名）
    tree = await client.get(f"/api/v1/projects/{pid}/tree", headers=user_headers)
    assert tree.status_code == 200


async def test_rename_same_name_is_noop(client, user_headers):
    """改成同名：不加后缀、不报错。"""
    pid = (await client.post("/api/v1/projects", json={"name": "同名"},
                             headers=user_headers)).json()["_id"]
    r = await client.patch(f"/api/v1/projects/{pid}", json={"name": "同名"},
                           headers=user_headers)
    assert r.status_code == 200 and r.json()["dir_name"] == "同名"


async def test_rename_onto_existing_name_409(client, user_headers):
    """改名撞上别人已用的显示名 → 409，不覆盖、不改动。"""
    await client.post("/api/v1/projects", json={"name": "被占"}, headers=user_headers)
    pid = (await client.post("/api/v1/projects", json={"name": "另一个"},
                             headers=user_headers)).json()["_id"]
    r = await client.patch(f"/api/v1/projects/{pid}", json={"name": "被占"},
                           headers=user_headers)
    assert r.status_code == 409
    names = sorted(p["name"] for p in (await client.get("/api/v1/projects",
                                                        headers=user_headers)).json())
    assert names == ["另一个", "被占"]


async def test_rename_missing_404_and_bad_name_422(client, user_headers):
    assert (await client.patch("/api/v1/projects/nope", json={"name": "x"},
                               headers=user_headers)).status_code == 404
    pid = (await client.post("/api/v1/projects", json={"name": "p"},
                             headers=user_headers)).json()["_id"]
    assert (await client.patch(f"/api/v1/projects/{pid}", json={"name": "   "},
                               headers=user_headers)).status_code == 422


async def test_create_same_name_as_different_user_ok(client, user_headers, admin_headers):
    """唯一性按用户隔离：别人用过的名字，我仍可建。"""
    await client.post("/api/v1/projects", json={"name": "共享名"}, headers=user_headers)
    r = await client.post("/api/v1/projects", json={"name": "共享名"}, headers=admin_headers)
    assert r.status_code == 201


async def test_rename_to_own_name_ok(client, user_headers):
    """改回自己原名不算撞名（校验时排除自己）。"""
    pid = (await client.post("/api/v1/projects", json={"name": "本体"},
                             headers=user_headers)).json()["_id"]
    r = await client.patch(f"/api/v1/projects/{pid}", json={"name": "本体"},
                           headers=user_headers)
    assert r.status_code == 200 and r.json()["dir_name"] == "本体"
