"""权限基座（23 §6：十类动作 × 角色；后端每次请求校验 用户 + 家庭 + 对象 + 动作 + 范围）。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.core.ids import new_id
from app.db.models.identity import MemberScope, RolePermission

# 十类动作
ALL_ACTIONS = [
    "view", "create_draft", "edit_draft", "confirm", "reverse",
    "correct", "archive", "delete", "configure", "audit_view",
]

# 角色默认动作集合（可被 role_permissions 表覆盖）
DEFAULT_ROLE_ACTIONS: dict[str, set[str]] = {
    "owner": set(ALL_ACTIONS),
    "admin": set(ALL_ACTIONS),
    "member": {"view", "create_draft", "edit_draft", "confirm", "correct"},
    "readonly": {"view"},
}

ROLE_ZH = {
    "owner": "家庭所有者",
    "admin": "系统管理员",
    "member": "家庭成员",
    "readonly": "只读成员",
}

# 家庭成员的可选角色与状态：新增/修改成员的接口统一按这两个元组校验，
# 避免一处校验、另一处直接写库（曾导致可以把角色改成任意字符串）。
MEMBER_ROLES = tuple(ROLE_ZH)
MEMBER_STATUSES = ("active", "disabled")


def default_actions_for(role: str) -> set[str]:
    return set(DEFAULT_ROLE_ACTIONS.get(role, {"view"}))


async def load_role_actions(db: AsyncSession, role: str, household_id: str | None = None) -> set[str]:
    """优先读取数据库中的角色动作集合，缺失时回退默认值。"""
    # 注意：RolePermission 目前没有 household_id 列，角色动作是全局的。
    # 家庭自定义角色需要先补该列与写入入口，再在这里加家庭维度过滤。
    result = await db.execute(
        select(RolePermission).where(
            RolePermission.role_code == role,
            RolePermission.allowed.is_(True),
        )
    )
    rows = list(result.scalars().all())
    if not rows:
        return default_actions_for(role)
    return {r.action for r in rows}


def require_action(user, action: str) -> None:
    if action not in user.action_set:
        raise AppError(ErrorCode.FORBIDDEN, status_code=403)


async def seed_role_permissions(db: AsyncSession) -> None:
    """写入默认角色动作集合（M0 基座种子）。"""
    existing = await db.execute(select(RolePermission).limit(1))
    if existing.scalars().first() is not None:
        return
    for role, actions in DEFAULT_ROLE_ACTIONS.items():
        for action in ALL_ACTIONS:
            db.add(
                RolePermission(
                    id=new_id(),
                    role_code=role,
                    action=action,
                    resource="*",
                    allowed=action in actions,
                )
            )


async def allowed_member_ids(db: AsyncSession, household_id: str, user_id: str) -> list[str]:
    """成员可查看范围（**按设计不启用**，保留接口以备将来扩展）。

    产品决定（2026-10-31 明确）：家庭是一个信任单元，成员之间互相可见、可编辑，
    不做「只看自己那份」的对象级数据隔离。所以：

    - 本函数不被任何查询调用，`member_scopes` 表也没有写入入口；
    - 成员之间的差异只体现在**动作级权限**——`readonly` 不能编辑/删除/配置，
      由 `role_permissions` + `require_action` 生效，这一层是真实有效的；
    - 若将来确需「孩子只看零花钱账户」，必须**写入入口与查询过滤两头一起接**，
      只改一头等于假隔离（后端仍会返回全部数据，只是前端没显示）。

    审计时不要把「未启用」当成缺陷——这是有意为之，不是没做完。
    """
    result = await db.execute(
        select(MemberScope).where(
            MemberScope.household_id == household_id,
            MemberScope.user_id == user_id,
            MemberScope.scope_type == "member",
        )
    )
    rows = [r for r in result.scalars().all() if r.scope_ref_id]
    return [r.scope_ref_id for r in rows if r.scope_ref_id]
