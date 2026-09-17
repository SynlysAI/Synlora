"""读取 CSV 并将列名和数据行数写入 UTF-8 JSON。"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def summarize_csv(input_path: Path) -> dict[str, object]:
    """统计 CSV 的列名和数据行数。

    Args:
        input_path: 输入 CSV 路径。

    Returns:
        含 columns 和 row_count 的摘要。
    """
    with input_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        try:
            columns = next(reader)
        except StopIteration:
            columns = []
        row_count = sum(1 for _row in reader)
    return {"columns": columns, "row_count": row_count}


def main() -> None:
    """解析命令行参数并写出摘要 JSON。"""
    parser = argparse.ArgumentParser(description="生成 CSV 结构摘要")
    parser.add_argument("--input", required=True, type=Path, help="输入 CSV 路径")
    parser.add_argument("--output", required=True, type=Path, help="输出 JSON 路径")
    args = parser.parse_args()
    summary = summarize_csv(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
