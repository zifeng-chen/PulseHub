"""前端资产表单字段必须能被后端接住。

历史上出现过：住宅表单填了「物业费单价 / 参考单价」，后端扩展字段白名单里
没有这两个键，保存时被静默丢弃，用户再打开编辑发现是空的。

这条测试把「前端表单绑定的字段」与「后端扩展字段白名单」对齐：
任何只在表单里存在的字段都会失败，逼着把模型 / 白名单 / 序列化一起补齐。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services import asset_extensions as ext

ASSET_COMPONENTS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "components" / "asset"

# 组件 → (资产类型, 绑定扩展字段的前缀)
COMPONENT_MAP = {
    "ResidenceFields.vue": ("real_estate", "extForm"),
    "ParkingFields.vue": ("parking", "form"),
    "VehicleFields.vue": ("vehicle", "form"),
    "MetalFields.vue": ("precious_metal", "form"),
}

# 这些组件里也会绑定通用资产字段（非扩展字段），不算缺失
BASE_FIELDS = {
    "name", "sub_type", "currency", "owner_member_id", "acquire_date", "acquire_cost",
    "current_value", "value_source", "depreciation_method", "maintenance_date",
    "linked_account_id", "include_in_net_worth", "note", "reason",
    "acquisition_mode", "acquisition_account", "acquisition_amount",
    "asset_type", "category_root_name",
}


def _bound_fields(path: Path, prefix: str) -> set[str]:
    text = path.read_text(encoding="utf-8")
    return set(re.findall(rf'v-model(?::[\w-]+)?="\s*{prefix}\.([a-zA-Z_0-9]+)', text))


@pytest.mark.parametrize("filename", sorted(COMPONENT_MAP))
def test_component_fields_are_accepted_by_backend(filename: str) -> None:
    asset_type, prefix = COMPONENT_MAP[filename]
    path = ASSET_COMPONENTS / filename
    if not path.exists():
        pytest.skip(f"未找到前端组件 {path}")

    bound = _bound_fields(path, prefix)
    allowed = set(ext.EXT_FIELDS[asset_type])
    unknown = sorted(bound - allowed - (BASE_FIELDS if prefix == "form" else set()))
    assert not unknown, (
        f"{filename} 绑定了后端不认识的扩展字段：{unknown}；"
        f"请同时补 model、EXT_FIELDS、_MONEY_FIELDS/枚举校验与 serialize"
    )
