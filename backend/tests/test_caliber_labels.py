"""统计口径的「键 ↔ 中文名」必须一一对应。

历史问题：后端新增 `liquidity` / `debt_service` / `asset_value` 三个口径项后，
前端标签映射没跟上，界面上直接显示出 snake_case 字段名，用户以为是数据库字段。

这里把约束钉在测试里：只要 CALIBER 加了 key 而 CALIBER_LABELS 没加，
CI 就会红，而不是等用户看到界面才发现。
"""
from app.services.stats import CALIBER, CALIBER_LABELS


def test_every_caliber_key_has_label():
    assert set(CALIBER) == set(CALIBER_LABELS)


def test_labels_are_chinese_and_non_empty():
    for key, label in CALIBER_LABELS.items():
        assert label.strip(), f"{key} 的中文名为空"
        assert not label.isascii(), f"{key} 的中文名疑似英文：{label}"
