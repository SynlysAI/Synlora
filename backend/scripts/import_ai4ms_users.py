"""从 AI4MS MongoDB 批量导入用户到 Synlora PostgreSQL。

导入规则：
- uid / username 均使用 AI4MS 的 username（保持登录习惯一致，显示名优先用 real_name）；
- password_hash 原样复制（pbkdf2_sha256 格式），登录验证时兼容校验并自动升级为 argon2；
- 角色固定为 user，部门与 --reference-uid 指定的参照用户（默认 xiaoxu）相同；
- 已存在相同 uid 或 username 的用户跳过，脚本可重复执行（幂等）；
- AI4MS 中 status 非 active 的用户跳过。

用法：
    python scripts/import_ai4ms_users.py [--dry-run] [--mongo-uri URI] [--reference-uid xiaoxu]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, UTC
from pathlib import Path

from dotenv import load_dotenv


APP_ROOT = Path(__file__).resolve().parents[1]
for import_path in (APP_ROOT, APP_ROOT / "package"):
    import_path_str = str(import_path)
    if import_path_str not in sys.path:
        sys.path.insert(0, import_path_str)

# AI4MS 生产 MongoDB（远程服务器，与本机 27018 的旧副本不是同一个实例）
DEFAULT_MONGO_URI = "mongodb://admin:password123@172.16.12.74:27018/ai4ms?authSource=admin"
DEFAULT_REFERENCE_UID = "xiaoxu"
# 与 docker-compose.yml 中 POSTGRES_URL 的默认值保持一致，供宿主机直连本地容器
DEFAULT_POSTGRES_URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/yuxi"


def load_project_env() -> None:
    """加载项目各级 .env，并兜底设置宿主机直连本地容器的 POSTGRES_URL。"""
    load_dotenv(APP_ROOT / ".env", override=False)
    load_dotenv(APP_ROOT.parent / ".env", override=False)
    load_dotenv(Path.cwd() / ".env", override=False)
    import os

    os.environ.setdefault("POSTGRES_URL", DEFAULT_POSTGRES_URL)


def _to_naive_utc(value: datetime | None) -> datetime | None:
    """将 pymongo 返回的 datetime 归一化为 naive UTC，匹配 PG TIMESTAMP 列。"""
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value


def fetch_ai4ms_users(mongo_uri: str) -> list[dict]:
    """从 AI4MS MongoDB 读取全部用户记录。

    Args:
        mongo_uri: MongoDB 连接字符串。

    Returns:
        users 集合的字典列表。
    """
    from pymongo import MongoClient

    with MongoClient(mongo_uri, serverSelectionTimeoutMS=5000) as client:
        db_name = "ai4ms"
        if "/" in mongo_uri.split("@", 1)[-1]:
            db_name = mongo_uri.split("@", 1)[-1].split("/", 1)[1].split("?", 1)[0]
        collection = client[db_name]["users"]
        return list(collection.find({}))


async def import_users(mongo_uri: str, reference_uid: str, dry_run: bool) -> int:
    """执行导入：跳过已存在用户，其余按参照用户的部门导入为普通用户。

    Args:
        mongo_uri: MongoDB 连接字符串。
        reference_uid: 部门参照用户的 uid。
        dry_run: 仅打印将要导入的用户，不写入数据库。

    Returns:
        进程退出码，0 表示成功。
    """
    from sqlalchemy import select

    from yuxi.storage.postgres.manager import pg_manager
    from yuxi.storage.postgres.models_business import User

    ai4ms_users = fetch_ai4ms_users(mongo_uri)

    pg_manager.initialize()
    async with pg_manager.get_async_session_context() as session:
        result = await session.execute(select(User))
        existing_users = list(result.scalars().all())
        existing_uids = {user.uid for user in existing_users}
        existing_usernames = {user.username for user in existing_users}

        reference = next((user for user in existing_users if user.uid == reference_uid), None)
        if reference is None:
            print(f"参照用户 {reference_uid} 不存在，无法确定导入部门。", file=sys.stderr)
            return 1

        imported: list[str] = []
        for record in ai4ms_users:
            ai4ms_username = str(record.get("username") or "").strip()
            if not ai4ms_username:
                print("跳过：AI4MS 记录缺少 username。", file=sys.stderr)
                continue
            if record.get("status") != "active":
                print(f"跳过：{ai4ms_username}（AI4MS 状态为 {record.get('status')}）")
                continue
            if ai4ms_username in existing_uids or ai4ms_username in existing_usernames:
                print(f"跳过：{ai4ms_username}（uid 或用户名已存在）")
                continue

            password_hash = str(record.get("password_hash") or "")
            if not password_hash.startswith("pbkdf2_sha256$"):
                print(f"跳过：{ai4ms_username}（无法识别的密码哈希格式）", file=sys.stderr)
                continue

            display_name = str(record.get("real_name") or "").strip() or ai4ms_username
            if dry_run:
                print(f"[dry-run] 将导入：{ai4ms_username}（显示名 {display_name}）")
                continue

            session.add(
                User(
                    username=display_name,
                    uid=ai4ms_username,
                    password_hash=password_hash,
                    role="user",
                    department_id=reference.department_id,
                    created_at=_to_naive_utc(record.get("created_at")),
                )
            )
            existing_uids.add(ai4ms_username)
            existing_usernames.add(display_name)
            imported.append(ai4ms_username)
            print(f"已导入：{ai4ms_username}（显示名 {display_name}，部门 {reference.department_id}）")

    if not dry_run:
        print(f"导入完成：共 {len(imported)} 个用户。")
    return 0


def main() -> int:
    """解析命令行参数并执行导入。"""
    import os

    parser = argparse.ArgumentParser(description="从 AI4MS MongoDB 批量导入用户")
    parser.add_argument(
        "--mongo-uri",
        default=os.getenv("AI4MS_MONGODB_URI", DEFAULT_MONGO_URI),
        help="AI4MS MongoDB 连接字符串（默认读取 AI4MS_MONGODB_URI 环境变量）",
    )
    parser.add_argument(
        "--reference-uid",
        default=DEFAULT_REFERENCE_UID,
        help=f"部门参照用户的 uid（默认 {DEFAULT_REFERENCE_UID}）",
    )
    parser.add_argument("--dry-run", action="store_true", help="仅打印将要导入的用户，不写入数据库")
    args = parser.parse_args()

    load_project_env()
    try:
        # Windows 下 psycopg 的 LangGraph 连接池不兼容 ProactorEventLoop，改用 SelectorEventLoop
        return asyncio.run(
            import_users(args.mongo_uri, args.reference_uid, args.dry_run),
            loop_factory=asyncio.SelectorEventLoop,
        )
    except Exception as exc:
        print(f"导入失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
