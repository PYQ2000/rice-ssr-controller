from __future__ import annotations

import time
from pathlib import Path


def save_adjustment(basename: str, value: float, base_dir: Path | None = None) -> Path:
    """
    将调整值保存到项目根目录下的 `adjustments/` 文件夹。
    文件名格式：<basename>_adj.txt
    每次写入会覆盖旧内容（如需累计可调整为追加）。
    """
    base = base_dir or Path(__file__).resolve().parent
    save_dir = base / "adjustments"
    save_dir.mkdir(exist_ok=True)
    adj_file = save_dir / f"{basename}_adj.txt"
    with open(adj_file, "w", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{value:.2f}\n")
    return adj_file

