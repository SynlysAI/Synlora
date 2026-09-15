"""签发 Spec_Agent 长期服务 token（供 Synlora 插件配置使用）。

背景：Spec_Agent 的 nmrserver 等接口要求登录（`AUTH_ENABLED=true`），其 token 是
HMAC-SHA256 自签、解析时回查 ai4ms 用户库（要求 sub 对应用户存在且 active）。
本脚本用 Spec_Agent 自己的 `AUTH_SECRET` 为一个已存在的 active 账号签一个长期 token，
填进 Synlora 管理后台「插件」页 → Spec_Agent → 配置 → 访问凭证。

用法（在 Synlora 仓库根或任意位置执行）：

    conda run -n synlysagent python docker/spec-agent/mint_token.py            # 自动挑第一个 active 用户，签 365 天
    conda run -n synlysagent python docker/spec-agent/mint_token.py --list     # 只列可用账号
    conda run -n synlysagent python docker/spec-agent/mint_token.py --username admin --days 730
    conda run -n synlysagent python docker/spec-agent/mint_token.py --user-id u-xxx --username admin --role admin

默认从 `E:/github_project/Spec_Agent/backend/.env` 读 `AUTH_SECRET` / `AUTH_MONGODB_URI` /
`AUTH_MONGODB_DATABASE`；可用 `--env-file` 指定其它路径。Mongo 不可达时可用
`--user-id/--username/--role` 直接指定账号（跳过查询）。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import sys
import time
from pathlib import Path

DEFAULT_ENV_FILE = Path("E:/github_project/Spec_Agent/backend/.env")
DEFAULT_DAYS = 365


def read_env(path: Path) -> dict[str, str]:
    """读取 .env 文件为字典（只认 KEY=VALUE 行）。

    Args:
        path: .env 文件路径。

    Returns:
        键值字典；文件不存在时返回空字典。
    """
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def sign(secret: str, payload: dict) -> str:
    """按 Spec_Agent 的算法签发 token（与 app/core/auth.py 逐字一致）。

    Args:
        secret: Spec_Agent 的 AUTH_SECRET。
        payload: token 负载（sub/username/role/exp/iat）。

    Returns:
        `{payload_b64}.{hmac_hex}` 形式的 token。
    """
    seg = base64.urlsafe_b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("utf-8").rstrip("=")
    sig = hmac.new(secret.encode("utf-8"), seg.encode("utf-8"),
                   digestmod=hashlib.sha256).hexdigest()
    return f"{seg}.{sig}"


def fetch_users(uri: str, database: str, limit: int = 20) -> list[dict]:
    """从 ai4ms 用户库取可用账号（active）。

    Args:
        uri: Mongo 连接串。
        database: 库名。
        limit: 最多返回条数。

    Returns:
        账号字典列表（user_id/username/role/status）。
    """
    import pymongo

    client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=5000)
    docs = client[database]["users"].find(
        {"status": "active"},
        {"_id": 0, "user_id": 1, "username": 1, "role": 1, "status": 1},
    ).limit(limit)
    return list(docs)


def main() -> int:
    """命令行入口。

    Returns:
        进程退出码（0 = 成功）。
    """
    parser = argparse.ArgumentParser(description="签发 Spec_Agent 长期服务 token")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE),
                        help="Spec_Agent 的 .env 路径（读 AUTH_SECRET / AUTH_MONGODB_*）")
    parser.add_argument("--secret", default="", help="直接指定 AUTH_SECRET（覆盖 .env）")
    parser.add_argument("--user-id", default="", help="账号 user_id（跳过用户库查询）")
    parser.add_argument("--username", default="", help="账号名（--list 之外建议显式指定）")
    parser.add_argument("--role", default="", help="角色 admin|user（默认取库中值，否则 user）")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="有效期天数")
    parser.add_argument("--list", action="store_true", help="只列可用账号，不签发")
    args = parser.parse_args()

    env = read_env(Path(args.env_file))
    secret = args.secret or env.get("AUTH_SECRET", "")
    if not secret:
        print(f"[!] 未取到 AUTH_SECRET（{args.env_file} 为空或未设置）——"
              f"请用 --secret 指定，或确认 Spec_Agent 配置了 AUTH_SECRET", file=sys.stderr)
        return 2

    user_id, username, role = args.user_id, args.username, args.role
    if not (user_id and username):
        uri = env.get("AUTH_MONGODB_URI", "")
        database = env.get("AUTH_MONGODB_DATABASE", "ai4ms")
        if not uri:
            print("[!] .env 未配置 AUTH_MONGODB_URI，且未用 --user-id/--username 指定账号",
                  file=sys.stderr)
            return 2
        try:
            users = fetch_users(uri, database)
        except Exception as exc:  # 连不上就退回手工指定
            print(f"[!] 用户库不可达（{exc}）；请改用 --user-id/--username/--role 指定",
                  file=sys.stderr)
            return 2
        if not users:
            print("[!] 用户库里没有 active 账号", file=sys.stderr)
            return 2
        if args.list:
            print("可用账号：")
            for u in users:
                print(f"  user_id={u.get('user_id')} username={u.get('username')} role={u.get('role')}")
            return 0
        picked = next((u for u in users if u.get("username") == username), None) if username else users[0]
        if picked is None:
            print(f"[!] 用户库中没有 username={username}", file=sys.stderr)
            return 2
        user_id = user_id or str(picked.get("user_id") or "")
        username = username or str(picked.get("username") or "")
        role = role or str(picked.get("role") or "user")
    role = role or "user"
    if role not in ("admin", "user"):
        print(f"[!] role 必须是 admin 或 user（收到 {role}）", file=sys.stderr)
        return 2
    if not user_id:
        print("[!] 缺少 user_id（Spec_Agent 解析 token 时会回查该 id）", file=sys.stderr)
        return 2

    now = int(time.time())
    expires_at = now + args.days * 86400
    token = sign(secret, {"sub": user_id, "username": username, "role": role,
                          "exp": expires_at, "iat": now})
    print("账号   :", f"{username}（user_id={user_id}, role={role}）")
    print("有效期 :", f"{args.days} 天（至 {time.strftime('%Y-%m-%d', time.localtime(expires_at))}）")
    print("token  :", token)
    print()
    print("下一步：Synlora 管理后台 →「插件」→ Spec_Agent 谱图解析 → 配置 → 访问凭证 → 粘贴上面 token → 保存")
    print("（token 是长期凭证，请勿提交进仓库或发给无关人员；需要轮换时重跑本脚本即可）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
