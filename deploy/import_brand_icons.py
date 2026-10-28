"""从 iconfont 图标集合导入银行 logo（纯 logo，不带中文字标）。

为什么需要它：`frontend/public/brands/` 里有几家银行还是位图（PNG），
放大或深色主题下会发虚。iconfont 的「银行LOGO合集」提供矢量源，
但里面 610 个图标混了「组合款」（logo + 中文字标），组合款塞进 60×36
的卡片框会糊成一团，所以这里只取纯 logo，并且只取项目真用得上的银行。

用法：
    # 预览到临时目录（不落库）
    python deploy/import_brand_icons.py --out .tmp/brands_preview
    # 正式写入 frontend/public/brands
    python deploy/import_brand_icons.py --out frontend/public/brands
    # 只导某几家
    python deploy/import_brand_icons.py --only cmb,ccb

处理细节：
- 去掉图标自带的白色圆角方块底（第一个白色 path），保留透明底，
  卡片本身有白底，圆角由卡片控制；
- 去掉 `width:1em;height:1em` 这类样式，改成 `width=100% height=100%`，
  否则当 <img> 用会缩成 1em；
- viewBox 原样保留 1024 画布。iconfont 的 logo 只占画布约 66%，直接塞进
  60×36 的卡片框会显得很小，所以导入后还要用浏览器量 `getBBox()` 把 viewBox
  收紧到内容边界 + 3% 留白（一次性处理，结果已入库；重新导入时需再收紧一次）。

**保留现有图标的两个例外**（新图标不适合小尺寸，故不覆盖）：
- `ceb`（光大）：集合里是 "Everbright Bank" 英文文字标，宽高比 2.5:1，60×36 下认不出；
- `pab`（平安）：集合里是「中国平安」橙色组合框，含中文且过密。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

COLLECTION_ID = 23316  # 银行LOGO合集（作者：小白大志）
API = "https://www.iconfont.cn/api/collection/detail.json?id={cid}"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# 项目 BrandIcon 里已支持的银行 → 该集合里的图标名（只收纯 logo，不带「（组合）」）
BANK_ICONS: dict[str, str] = {
    "icbc": "中国工商银行logo",
    "ccb": "建设银行logo",
    "abc": "中国农业银行logo",
    "boc": "中国银行logo",
    "bocom": "交通银行logo",
    "psbc": "中国邮政储蓄银行logo",
    "cmb": "招商银行logo",
    "spdb": "浦发银行",
    "citic": "中信银行logo",
    "cmbc": "中国民生银行logo",
    "cib": "兴业银行logo",
    "ceb": "中国光大银行logo",
    "hxb": "华夏银行logo",
    "pab": "平安银行logo",
    "cgb": "广发银行logo",
    "bob": "北京银行logo",
    "bos": "上海银行logo",
    "nbcb": "宁波银行logo",
}


# 暂不替换的两家：新图标是文字标/中文组合框，60×36 下不如现有图标清楚
SKIP_KEYS = {"ceb", "pab"}


def fetch_collection(cid: int = COLLECTION_ID) -> dict[str, str]:
    """返回 {图标名: svg 源码}。只取纯 logo，组合款直接丢弃。"""
    req = urllib.request.Request(
        API.format(cid=cid), headers={"User-Agent": UA, "Referer": f"https://www.iconfont.cn/collections/detail?cid={cid}"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))["data"]
    icons: dict[str, str] = {}
    for item in data.get("icons") or []:
        name = item.get("name") or ""
        if not name or "（组合）" in name or "(组合)" in name:
            continue
        svg = (item.get("show_svg") or "").strip()
        if svg.startswith("<svg"):
            icons[name] = svg
    return icons


def _strip_white_background(svg: str) -> str:
    """去掉第一个「白色圆角方块」path。

    判定：该 path 的 fill 是白色，且排在第一位（iconfont 的统一画布底）。
    只删这一个，白色细节（如上海银行 logo 里的白线条）必须保留。
    """
    paths = list(re.finditer(r"<path\b[^>]*/?>", svg))
    if not paths:
        return svg
    first = paths[0]
    tag = first.group(0)
    fill = re.search(r'fill="([^"]+)"', tag)
    is_white = fill and fill.group(1).replace("#", "").lower() in {"ffffff", "fff"}
    looks_like_canvas = "q" in tag and ("224" in tag or "223" in tag)
    if is_white and looks_like_canvas:
        return svg[: first.start()] + svg[first.end():]
    return svg


def _normalize(svg: str) -> str:
    svg = _strip_white_background(svg)
    # 根标签：统一命名空间、尺寸与 viewBox
    vb = re.search(r'viewBox="([^"]+)"', svg)
    view_box = vb.group(1) if vb else "0 0 1024 1024"
    body = svg[svg.index(">", svg.index("<svg")) + 1: svg.rindex("</svg>")]
    body = body.strip()
    # 去掉纯白填充的辅助元素（若还有残留的整块白底，交给上面的函数处理）
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view_box}" '
        f'width="100%" height="100%" preserveAspectRatio="xMidYMid meet">{body}</svg>\n'
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从 iconfont 导入银行纯 logo")
    parser.add_argument("--out", default="frontend/public/brands", help="输出目录")
    parser.add_argument("--only", default="", help="只导指定 key，逗号分隔（如 cmb,ccb）")
    parser.add_argument("--cid", type=int, default=COLLECTION_ID, help="iconfont 集合 id")
    args = parser.parse_args(argv)

    keys = [k.strip() for k in args.only.split(",") if k.strip()] or list(BANK_ICONS)
    if not args.only:
        keys = [k for k in keys if k not in SKIP_KEYS]
    unknown = [k for k in keys if k not in BANK_ICONS]
    if unknown:
        print(f"未知 key：{', '.join(unknown)}；可选：{', '.join(BANK_ICONS)}", file=sys.stderr)
        return 2

    print(f"拉取集合 {args.cid} …")
    icons = fetch_collection(args.cid)
    print(f"集合内纯 logo {len(icons)} 个")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    written, missing = [], []
    for key in keys:
        name = BANK_ICONS[key]
        svg = icons.get(name)
        if not svg:
            missing.append(f"{key}({name})")
            continue
        (out_dir / f"{key}.svg").write_text(_normalize(svg), encoding="utf-8")
        written.append(f"{key}.svg")

    print(f"已写入 {len(written)} 个 → {out_dir}")
    if missing:
        print(f"集合里没找到：{', '.join(missing)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
