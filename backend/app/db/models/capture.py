"""智能录入与 OCR 调用记录模型（06 §10、09 §47、10 §1）。

流程：上传附件 → OCR 识别（ocr_call_logs）→ 结构化 → 用户审核 → 生成草稿事件。
- OCR 失败可手工录入，不阻塞手工记账；
- OCR 调用记录保存原文与结构化结果，支持失败重试；
- 不自动确认 OCR 结果：结构化结果仅供用户审核，确认后才形成正式事实。
"""

from __future__ import annotations

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, BaseColumns


class OcrCallLog(Base, BaseColumns):
    """OCR 调用记录（10 §1、16 §16：记录原文、结构化结果、置信度、耗时与错误）。"""

    __tablename__ = "ocr_call_logs"

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    attachment_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    material_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider: Mapped[str] = mapped_column(String(32), default="baidu")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending/success/failed
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # 结构化结果 JSON
    confidence: Mapped[float | None] = mapped_column(Integer, nullable=True)  # 0-100
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


__all__ = ["OcrCallLog"]
