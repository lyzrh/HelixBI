"""开发/测试种子：一键创建 admin / analyst / viewer 三个测试账号（默认工作区）。

用法：python -m backend.devseed
- 幂等：已存在则跳过（可重复执行）。
- 仅用于本地开发与联调验证；显式执行才会创建，不进入自动 seed，
  不影响生产默认配置（自动 seed 仍只创建内置 admin）。
- 生产环境（HELIX_ENV=production）下拒绝执行。
"""

import os
import sys

from backend.auth.security import hash_password


def run() -> None:
    if os.getenv("HELIX_ENV", "").lower() == "production":
        print("[devseed] HELIX_ENV=production，拒绝创建测试账号")
        sys.exit(1)

    from backend.db import SessionLocal, init_db
    from backend.models import Role, User, Workspace, WorkspaceMember

    init_db()
    dev_users = [
        ("analyst1", "analyst", "Dev-12345678"),
        ("viewer1", "viewer", "Dev-12345678"),
    ]
    with SessionLocal() as db:
        ws = db.query(Workspace).order_by(Workspace.id).first()
        if not ws:
            print("[devseed] 未找到任何工作区，请先正常启动一次服务完成初始化")
            sys.exit(1)
        for username, role_code, password in dev_users:
            if not db.get(Role, role_code):
                print(f"[devseed] 角色 {role_code} 不存在（seed 未初始化？），跳过 {username}")
                continue
            user = db.query(User).filter(User.username == username).first()
            if not user:
                user = User(username=username, display_name=username,
                            email=f"{username}@dev.local",
                            password_hash=hash_password(password))
                db.add(user)
                db.flush()
                print(f"[devseed] 创建用户 {username}（{role_code}）")
            else:
                print(f"[devseed] 用户 {username} 已存在，跳过创建")
            member = (db.query(WorkspaceMember)
                      .filter(WorkspaceMember.user_id == user.id,
                              WorkspaceMember.workspace_id == ws.id).first())
            if not member:
                db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id,
                                       role_code=role_code))
                print(f"[devseed] {username} 加入工作区「{ws.name}」，角色 {role_code}")
            else:
                member.role_code = role_code
                print(f"[devseed] {username} 已是成员，角色确认为 {role_code}")
        db.commit()
    print("\n[devseed] 测试账号就绪：admin/admin123（内置）· analyst1/Dev-12345678 · viewer1/Dev-12345678")
    print("[devseed] 提醒：生产环境请勿使用上述口令；此处仅用于本地联调验证 RBAC。")


if __name__ == "__main__":
    run()
