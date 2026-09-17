"""任务查询 API 测试。"""
import pytest

from app.db.repos import JobRepo


@pytest.fixture
async def seeded_jobs(app):
    """落三条任务：两条属 u-user（分属两会话）、一条属他人。

    注意 conftest 的 user_headers 签发 sub 为 "u-user"。
    """
    repo = JobRepo(app.state.store)
    mine = await repo.create({"kind": "k", "status": "pending", "session_id": "sa",
                              "user_id": "u-user", "external_id": "e1",
                              "backend": "external"})
    mine2 = await repo.create({"kind": "k", "status": "completed", "session_id": "sb",
                               "user_id": "u-user", "external_id": "e2",
                               "backend": "external"})
    other = await repo.create({"kind": "k", "status": "pending", "session_id": "sa",
                               "user_id": "u-other", "external_id": "e3",
                               "backend": "external"})
    return {"session_a": "sa", "mine": mine["_id"],
            "mine2": mine2["_id"], "other": other["_id"]}


async def test_list_jobs_requires_auth(client):
    """未带凭证一律 401。"""
    resp = await client.get("/api/v1/jobs")
    assert resp.status_code == 401


async def test_list_jobs_scoped_to_current_user(client, user_headers, seeded_jobs):
    """只返回当前用户的任务；可按 session_id 过滤。"""
    resp = await client.get("/api/v1/jobs", headers=user_headers)
    assert resp.status_code == 200
    assert {d["_id"] for d in resp.json()} == {
        seeded_jobs["mine"], seeded_jobs["mine2"]}
    only_one = await client.get(
        f"/api/v1/jobs?session_id={seeded_jobs['session_a']}", headers=user_headers)
    assert [d["_id"] for d in only_one.json()] == [seeded_jobs["mine"]]


async def test_get_job_detail_and_foreign_404(client, user_headers, seeded_jobs):
    """详情可见自己的任务；他人的任务 404。"""
    ok = await client.get(f"/api/v1/jobs/{seeded_jobs['mine']}", headers=user_headers)
    assert ok.status_code == 200
    assert ok.json()["_id"] == seeded_jobs["mine"]
    foreign = await client.get(
        f"/api/v1/jobs/{seeded_jobs['other']}", headers=user_headers)
    assert foreign.status_code == 404


async def test_get_unknown_job_404(client, user_headers):
    """不存在的任务 404。"""
    resp = await client.get("/api/v1/jobs/nope", headers=user_headers)
    assert resp.status_code == 404


async def test_cancel_job_requires_owner_and_reuses_service(client, user_headers,
                                                            seeded_jobs):
    """本人可直接取消，异用户任务仍以 404 隐藏。"""
    ok = await client.post(
        f"/api/v1/jobs/{seeded_jobs['mine']}/cancel", headers=user_headers
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "cancelled"
    foreign = await client.post(
        f"/api/v1/jobs/{seeded_jobs['other']}/cancel", headers=user_headers
    )
    assert foreign.status_code == 404
