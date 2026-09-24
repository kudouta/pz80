#!/usr/bin/env python3

import re

from pz80 import disasm

# 後方互換のため walk 名前空間からも参照できるようにしておく
# （実体は _vectors.py。walk -> disasm -> _vectors の依存を一方向に保つため）
from pz80._vectors import VECTOR_ALIASES, parse_entry

__all__ = [
    "VECTOR_ALIASES",
    "address_set",
    "build_addr_map",
    "classify_instruction",
    "merge_ranges",
    "parse_entry",
    "sweep_from",
    "trace",
    "walk",
]

_Z80_COND_CODES = {"NZ", "Z", "NC", "C", "PO", "PE", "P", "M"}

_RE_LABEL = re.compile(r"\bL_([0-9A-Fa-f]{4})\b")


def merge_ranges(sorted_addrs):
    """ソート済みアドレスリストを連続レンジのリストにまとめる。

    Args:
        sorted_addrs (list[int]): ソート済みアドレスリスト。

    Returns:
        list[list[int]]: [[start, end], ...] 形式のレンジリスト。
    """
    if not sorted_addrs:
        return []
    ranges = []
    start = end = sorted_addrs[0]
    for addr in sorted_addrs[1:]:
        if addr == end + 1:
            end = addr
        else:
            ranges.append([start, end])
            start = end = addr
    ranges.append([start, end])
    return ranges


def _get_branch_target(asm_str):
    """asm文字列から分岐先アドレスを抽出する。"""
    m = _RE_LABEL.search(asm_str)
    return int(m.group(1), 16) if m else None


def classify_instruction(asm_str):
    """命令を分類して (次アドレス継続フラグ, 分岐先アドレスorNone) を返す。

    Args:
        asm_str (str): 逆アセンブル済み命令文字列。

    Returns:
        tuple[bool, int | None]: (継続フラグ, 分岐先アドレス)。
            継続フラグが False の場合、現在のトレースパスを停止する。
    """
    tokens = asm_str.upper().split()
    if not tokens:
        return True, None
    mnemonic = tokens[0]

    if mnemonic in ("RETI", "RETN", "HALT"):
        return False, None

    if mnemonic == "RET":
        if len(tokens) == 1:
            return False, None  # 無条件 RET: 終端
        return True, None  # 条件付き RET cc: フォールスルー継続

    if mnemonic in ("JP", "JR"):
        second = tokens[1].rstrip(",") if len(tokens) > 1 else ""
        is_conditional = second in _Z80_COND_CODES
        target = _get_branch_target(asm_str)
        return (True, target) if is_conditional else (False, target)

    if mnemonic in ("CALL", "DJNZ"):
        return True, _get_branch_target(asm_str)

    if mnemonic == "RST":
        # RST nn はサブルーティン呼び出し: フォールスルーしつつターゲットも追跡
        try:
            target = int(tokens[1], 0)
            return True, target
        except (IndexError, ValueError):
            return True, None

    return True, None


def address_set(start, size, valid_ranges=None):
    """解析対象となる有効アドレスの集合を返す。

    Args:
        start (int): ロードアドレス。valid_ranges 指定時は無視される。
        size (int): データ長。
        valid_ranges (list[list[int]] | None): 有効なアドレス範囲 [[start, end], ...]。

    Returns:
        set[int]: 有効アドレスの集合。
    """
    if valid_ranges is None:
        return set(range(start, start + size))
    addrs = set()
    for r in valid_ranges:
        addrs.update(range(r[0], r[1] + 1))
    return addrs


def build_addr_map(data, start=0, m1_handler=None, valid_ranges=None):
    """バイナリを逆アセンブルして アドレス → 命令 のマップを構築する。

    Args:
        data (bytes | list[int]): 解析対象のバイナリデータ。
        start (int): ロードアドレス。valid_ranges 指定時は 0 基点として扱う。
        m1_handler (callable | None): M1サイクル復号ハンドラー。
        valid_ranges (list[list[int]] | None): 有効なアドレス範囲。
            指定時はギャップ領域の命令をマップから除外する。

    Returns:
        dict[int, dict]: アドレスをキーとする逆アセンブル結果。
    """
    size = len(data)
    if size == 0:
        return {}

    disasm_start = 0 if valid_ranges is not None else start

    ope = disasm.Disasm()
    if m1_handler:
        ope.m1_handler = m1_handler
    out = ope.exec(disasm_start, list(data), size)
    addr_map = {p["address"]: p for p in out if p.get("opcode")}

    if valid_ranges is not None:
        valid = address_set(start, size, valid_ranges)
        addr_map = {a: p for a, p in addr_map.items() if a in valid}
    return addr_map


def sweep_from(data, addr, base=0, m1_handler=None):
    """指定アドレスから線形に逆アセンブルして アドレス → 命令 のマップを返す。

    `build_addr_map()` が作るのは**先頭から 1 回走査したときの命令境界**だけです。
    分岐先がその境界に乗らないことがあります。

        0x0005  C3 3E 30   先頭からの走査は JP L_303E と 3 バイト読む
        0x0006             ここが本当の命令先頭だが、境界になっていない

    `0x0000: JP L_0006` が実在するのに、0x0006 が map に無いため `trace()` が
    無言で追跡を打ち切り、その先がまるごとデータ扱いになっていました。
    linear sweep で境界を先に決めてしまうと recursive descent が入れない、という
    衝突です。分岐先から走査し直して境界を作るために使います。
    （この形は `tests/test_walk.py` の 9 バイトのイメージで固定してあります）

    Args:
        data (bytes | list[int]): 解析対象のバイナリデータ。
        addr (int): 走査を始めるアドレス。
        base (int): `data[0]` が置かれているアドレス。
        m1_handler (callable | None): M1サイクル復号ハンドラー。

    Returns:
        dict[int, dict]: アドレスをキーとする逆アセンブル結果。範囲外なら空。
    """
    offset = addr - base
    if offset < 0 or offset >= len(data):
        return {}

    ope = disasm.Disasm()
    if m1_handler:
        ope.m1_handler = m1_handler
    out = ope.exec(addr, list(data)[offset:], len(data) - offset)
    return {p["address"]: p for p in out if p.get("opcode")}


def trace(addr_map, entries, resweep=None, unresolved=None):
    """エントリポイントからCFGをトレースする。

    Args:
        addr_map (dict[int, dict]): build_addr_map() の戻り値。
        entries (iterable[int]): エントリポイントアドレス。
        resweep (callable | None): `addr_map` に無いアドレスに当たったとき、
            そこから走査し直して追加の命令マップを返す関数（`sweep_from()` を
            束縛したもの）。**与えないと、境界に乗らない分岐先で追跡が静かに
            止まります**。
        unresolved (set | None): 復号できなかった分岐先を書き出す集合。
            イメージの外（RAM・I/O）へ飛ぶ場合はここに残ります。

    Returns:
        tuple[set[int], set[int]]: (到達したバイトの集合, 命令先頭アドレスの集合)。
            命令先頭集合は、あるアドレスが命令の途中かどうかの判定に使う。

    Note:
        `resweep` で見つけた命令は **`addr_map` へ追加します**（呼び出し側の
        辞書を書き換えます）。返す命令先頭集合にそれらのアドレスが含まれるので、
        呼び出し側が `addr_map[a]` を引けないと辻褄が合わなくなるためです。
    """
    code_addrs = set()
    heads = set()
    to_visit = set(entries)
    visited_starts = set()

    while to_visit:
        addr = to_visit.pop()
        if addr in visited_starts:
            continue
        visited_starts.add(addr)

        while True:
            p = addr_map.get(addr)
            if p is None and resweep is not None:
                # 先頭からの走査が作った境界に乗らない分岐先。そこから読み直す。
                for a, q in resweep(addr).items():
                    addr_map.setdefault(a, q)
                p = addr_map.get(addr)
            if p is None:
                if unresolved is not None:
                    unresolved.add(addr)
                break

            if addr in code_addrs:
                break

            instr_len = len(p["opcode"])
            heads.add(addr)
            for i in range(instr_len):
                code_addrs.add(addr + i)

            continues, branch_target = classify_instruction(p["asm"])

            if branch_target is not None:
                to_visit.add(branch_target)

            if not continues:
                break

            addr += instr_len

    return code_addrs, heads


def walk(
    data,
    start=0,
    extra_entries=None,
    valid_ranges=None,
    m1_handler=None,
    unresolved=None,
):
    """CFGトレースによりデータ領域を検出する。

    バイナリデータを逆アセンブルし、エントリポイントから制御フローをトレースする。
    到達できなかったアドレス範囲をデータ領域として返す。

    Args:
        data (bytes | list[int]): 解析対象のバイナリデータ。
        start (int): メインエントリポイントアドレス (デフォルト: 0x0000)。
            valid_ranges が None の場合はロードアドレスも兼ねる。
        extra_entries (list[str | int] | None): 追加エントリポイントのリスト。
            文字列 (RESET/RST0-7/IM1/NMI) または整数アドレスを指定する。
        valid_ranges (list[list[int]] | None): 有効なアドレス範囲 [[start, end], ...]。
            指定時は data をアドレス 0 基点の配列として扱い、ギャップ領域を出力から除外する。
            複数ファイルを異なるアドレスに配置する場合に使用する。
        m1_handler (callable | None): M1サイクル復号ハンドラー。
            (address: int, byte: int) -> int の形式。暗号化ROMのCFGトレースに使用する。
        unresolved (set | None): 復号できなかった分岐先を書き出す集合。
            イメージの外や `valid_ranges` の隙間へ飛ぶものがここに残る。

            **正常なら空になる。** 手書きの Z80 コードは ROM の外へ分岐しない。
            実 ROM 3 本で測っていずれも 0 件だった。1 件でも出たら
            **`bins` の指定漏れか、ROM ファイルの欠落を疑う**。

    Returns:
        list[list[int]]: データ領域の [[start, end], ...] リスト。

    Raises:
        ValueError: extra_entries に無効な値が含まれる場合。

    Example:
        >>> with open("rom.bin", "rb") as f:
        ...     binary = f.read()
        >>> regions = walk(binary, start=0x0000, extra_entries=["NMI", "IM1"])
        >>> print(regions)
        [[0x1000, 0x12FF], [0x2000, 0x2FFF]]
    """
    size = len(data)
    if size == 0:
        return []

    all_addrs = address_set(start, size, valid_ranges)

    # エントリポイント収集
    entry_points = {start}
    for e in extra_entries or []:
        entry_points.add(parse_entry(e))

    addr_map = build_addr_map(data, start, m1_handler, valid_ranges)

    # 分岐先が先頭からの走査の命令境界に乗らないことがある。そこから読み直せる
    # ようにしておく（詳細は sweep_from()）。valid_ranges 指定時は data を
    # アドレス 0 基点として扱うので、基点の取り方を build_addr_map と揃える。
    base = 0 if valid_ranges is not None else start
    valid = address_set(start, size, valid_ranges)

    def resweep(addr):
        if addr not in valid:
            return {}
        return {
            a: p
            for a, p in sweep_from(data, addr, base, m1_handler).items()
            if a in valid
        }

    code_addrs, _heads = trace(
        addr_map, entry_points, resweep=resweep, unresolved=unresolved
    )

    # データ領域 = 有効アドレス全体 - コード領域
    return merge_ranges(sorted(all_addrs - code_addrs))
