#!/usr/bin/env python3
"""
z80_opcodes.yaml から z80.py の _codetbl セクションを自動生成するスクリプト。

使用方法:
    python tools/generate_codetbl.py

z80_opcodes.yaml を参照データとして読み込み、src/pz80/z80.py 内の
BEGIN GENERATED / END GENERATED マーカー間のコードを置き換えます。
"""

import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("Error: pyyaml is required. Install it with: pip install pyyaml")
    sys.exit(1)

YAML_PATH = Path("src/pz80/z80_opcodes.yaml")
Z80_PATH = Path("src/pz80/z80.py")
BEGIN_MARKER = "    # <<< BEGIN GENERATED FROM z80_opcodes.yaml >>>"
END_MARKER = "    # <<< END GENERATED >>>"
INDENT = "        "  # 8スペース（クラス本体のインデント）

# z80.py の _codetbl へ出力する既知フィールド
KNOWN_FIELDS = {"code", "bytes", "asm", "type", "rel", "jmp", "ext", "undoc"}

# 意図的に z80.py へ渡さないフィールド。
# alt_encoding は「同じ命令の別エンコード」を示すテスト専用メタデータで、
# 参照元はリポジトリ側のテストのみ（DD/FD 対称性比較のキーに使う）。
#
# **落としてよいのは、区別をニーモニックが担っているからである。** 冗長エンコードの
# 2 件（ED63 / ED6B）は asm の先頭が `ld.alt` なので、_asm_map のキーが正準側
# （0x22 / 0x2A）と衝突しない。
#
# 以前は両方 `ld` で、この情報が無いために後勝ちで ED 側が選ばれ、**公認命令が
# 4 バイトで出力されていた**。「影響しない」と書いて落としていたが、落としたことが
# 影響を生んでいた。綴りを分けない冗長エンコードを将来足すなら、ここを見直すこと。
# 経緯はリポジトリ側の変更記録に残してある。
DROPPED_FIELDS = {"alt_encoding"}


def format_entry(entry: dict) -> str:
    """YAMLエントリを Python の dict リテラル文字列に変換する。

    Args:
        entry: z80_opcodes.yaml の1エントリ（dict）。

    Returns:
        _codetbl に埋め込む dict リテラル1行分の文字列。

    Raises:
        ValueError: KNOWN_FIELDS にも DROPPED_FIELDS にも属さない未知の
            フィールドが含まれる場合。YAML へフィールドを追加したまま
            本スクリプトを追随させ忘れると z80.py へ伝播せず（silently drop）、
            テストでも検出できないため、ここで検査する。
    """
    unknown = set(entry) - KNOWN_FIELDS - DROPPED_FIELDS
    if unknown:
        code = " ".join(f"0x{b:02X}" for b in entry.get("code") or []) or "(no code)"
        raise ValueError(
            f"Unknown field(s) {sorted(unknown)} on the entry with code=[{code}]. "
            f"Add them to KNOWN_FIELDS to emit them, or to DROPPED_FIELDS "
            f"(with a reason) if they must not reach z80.py."
        )

    parts = []

    # code: バイト値リストを 0xNN リテラル表記で出力
    code_items = ", ".join(f"0x{b:02X}" for b in entry["code"]) if entry["code"] else ""
    parts.append(f'"code": [{code_items}]')

    parts.append(f'"bytes": {entry["bytes"]}')

    # asm: 文字列リストをそのまま出力
    asm_items = ", ".join(f'"{t}"' for t in entry["asm"])
    parts.append(f'"asm": [{asm_items}]')

    if "type" in entry:
        parts.append(f'"type": "{entry["type"]}"')
    if "rel" in entry:
        parts.append(f'"rel": {entry["rel"]}')
    if "jmp" in entry:
        parts.append(f'"jmp": {entry["jmp"]}')
    if "ext" in entry:
        parts.append(f'"ext": 0x{entry["ext"]:02X}')
    if "undoc" in entry:
        parts.append(f'"undoc": {entry["undoc"]}')

    return INDENT + "{" + ", ".join(parts) + "},"


def generate_codetbl(entries: list) -> list[str]:
    """エントリリストから _codetbl の Python コード行リストを生成する。"""
    lines = []
    lines.append("    _codetbl = [\n")

    # セクションコメント: (code_bytes, asm[0]) の組み合わせで先頭エントリを識別
    SECTION_COMMENTS = [
        ([0x00], "nop", "        # 主命令 (op)\n"),
        ([0xCB, 0x00], "rlc", "        # CBビット命令 (CB op)\n"),
        ([0xED, 0x40], "in", "        # ED拡張命令 (ED op)\n"),
        ([0xDD, 0x09], "add", "        # IX命令 (DD op)\n"),
        ([0xDD, 0xCB], "rlc", "        # IXビット命令 (DD CB op)\n"),
        ([0xFD, 0x09], "add", "        # IY命令 (FD op)\n"),
        ([0xFD, 0xCB], "rlc", "        # IYビット命令 (FD CB op)\n"),
    ]

    def code_of(e):
        return list(e["code"]) if e["code"] else []

    for entry in entries:
        code = code_of(entry)
        mnemonic = entry["asm"][0] if entry["asm"] else ""
        for sec_code, sec_mnemonic, comment in SECTION_COMMENTS:
            if code == sec_code and mnemonic == sec_mnemonic:
                lines.append(comment)
                break

        lines.append(format_entry(entry) + "\n")

    lines.append("    ]\n")
    return lines


def main():
    if not YAML_PATH.exists():
        print(f"Error: {YAML_PATH} not found.")
        sys.exit(1)
    if not Z80_PATH.exists():
        print(f"Error: {Z80_PATH} not found.")
        sys.exit(1)

    with open(YAML_PATH, encoding="utf-8") as f:
        entries = yaml.safe_load(f)

    print(f"Loaded {len(entries)} entries from YAML")

    # z80.py を読み込み、マーカー間を置き換える
    with open(Z80_PATH, encoding="utf-8") as f:
        z80_lines = f.readlines()

    begin_idx = end_idx = None
    for i, line in enumerate(z80_lines):
        if line.rstrip() == BEGIN_MARKER:
            begin_idx = i
        elif line.rstrip() == END_MARKER:
            end_idx = i
            break

    if begin_idx is None or end_idx is None:
        print("Error: generated-section markers not found in z80.py.")
        print(f"  BEGIN: {BEGIN_MARKER}")
        print(f"  END:   {END_MARKER}")
        sys.exit(1)

    generated = generate_codetbl(entries)

    new_lines = z80_lines[: begin_idx + 1] + generated + z80_lines[end_idx:]

    with open(Z80_PATH, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    print(f"Done: {Z80_PATH} updated.")


if __name__ == "__main__":
    main()
