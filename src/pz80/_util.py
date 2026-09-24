#!/usr/bin/env python3
"""pz80 内部ヘルパー関数。"""


def format_location(line, file):
    """エラーメッセージ用の位置情報文字列を生成する。

    Args:
        line (int): 行番号。
        file (str | None): ソース識別子。

    Returns:
        str: "line 5 in main.asm" または "line 5" の形式。
    """
    return f"line {line} in {file}" if file else f"line {line}"


def current_address(item):
    """`$` として解決する現在アドレスを返す。未確定なら None。

    ベースアドレスは Pass 0 で確定するが、ORG より前の行など未設定の場合がある。
    その場合は `$` を解決できないため None を返し、評価器側でエラーにさせる。

    Args:
        item (dict): アセンブル行。"base" と "offset" を参照する。

    Returns:
        int | None: base + offset。base が未設定・負なら None。
    """
    base = item.get("base")
    if base is None or base < 0:
        return None
    return base + item.get("offset", 0)
