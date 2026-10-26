"""请求上下文与当前用户（用于审计、权限与家庭边界）。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CurrentUser:
    user_id: str
    username: str
    display_name: str
    is_system_admin: bool
    household_id: str | None = None
    role: str = "member"
    session_id: str | None = None
    actions: set[str] = field(default_factory=set)

    @property
    def action_set(self) -> set[str]:
        return self.actions

    def can(self, action: str) -> bool:
        return action in self.actions


@dataclass
class RequestContext:
    request_id: str = ""
    ip_summary: str = ""
    user_agent: str = ""
    source_page: str | None = None
    source_method: str = "manual"
