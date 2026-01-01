#!/usr/bin/env python3

import re

from pz80 import z80


class Disasm:
    """Z80逆アセンブラクラス"""

    def __init__(self):
        """Disasmクラスを初期化します。"""
        self.cpu = z80.Z80()
        self._datamap = []  # 逆アセンブル時にデーターとして扱うアドレス範囲テーブル
        self._dispatch = {
            1: self._handle_1byte,
            2: self._handle_2bytes,
            3: self._handle_3bytes,
            4: self._handle_4bytes,
        }

    @property
    def datamap(self):
        """データマッププロパティ。

        Returns:
            list: データ範囲のリスト [[開始, 終了], ...]。
        """
        return self._datamap

    @datamap.setter
    def datamap(self, p):
        """データマップセッター。

        Args:
            p (list): データ範囲のリスト。
        """
        self._datamap = p

    def _tmpl(self, asm):
        """アセンブルリストからアセンブル文字列生成"""
        mnemonic = asm[0].upper()
        if len(asm) == 1:
            return mnemonic
        # Join operands, adding a space after commas for readability.
        operands_str = "".join(asm[1:]).replace(",", ", ")
        return f"{mnemonic} {operands_str}"

    def _reladdr(self, x, y):
        """相対アドレス計算"""
        maxword = 0xFFFF
        return (((x - 0x100) if (x & 0x80) else (x & 0x7F)) + y + 2) & maxword

    def _handle_1byte(self, u, opcode, adr):
        return self._tmpl(u["asm"])

    def _handle_2bytes(self, u, opcode, adr):
        tmpl = self._tmpl(u["asm"])
        if u.get("rel") is not None:
            return tmpl.replace("0x{0}", "L_{0:04X}").format(self._reladdr(opcode[1], adr))
        return tmpl.replace("{0}", "{0:02X}").format(opcode[1])

    def _handle_3bytes(self, u, opcode, adr):
        if u.get("jmp") is not None:
            return self._tmpl(u["asm"]).replace("0x{1}{0}", "L_{1:02X}{0:02X}").format(opcode[1], opcode[2])

        op_type = u.get("type")
        if op_type == "byte":
            return self._tmpl(u["asm"]).replace("{0}", "{0:02X}").format(opcode[2])

        if op_type == "word":
            return (
                self._tmpl(u["asm"])
                .replace("{0}", "{0:02X}")
                .replace("{1}", "{1:02X}")
                .format(opcode[1], opcode[2])
            )
        return None

    def _handle_4bytes(self, u, opcode, adr):
        if u.get("ext") is not None:
            # ddcb / fdcb
            return self._tmpl(u["asm"]).replace("{0}", "{0:02X}").format(opcode[2])

        op_type = u.get("type")
        if op_type in ("byte", "word"):
            return (
                self._tmpl(u["asm"])
                .replace("{0}", "{0:02X}")
                .replace("{1}", "{1:02X}")
                .format(opcode[2], opcode[3])
            )
        return None

    def op2asm(self, adr, opcode):
        """オペコードをアセンブリ文字列に変換します。

        Args:
            adr (int): 現在のアドレス。
            opcode (list): オペコードバイト列。

        Returns:
            str: アセンブリ文字列、または一致しない場合はNone。
        """
        # ------------------------------------------------------
        # ここからメイン
        # ------------------------------------------------------
        for p in self.datamap:
            if (p[0] <= adr) and (p[1] >= adr):
                return f"db 0x{opcode[0]:02X} ; [{self.cpu.strmap[opcode[0]]}]"

        # オペコード検索
        u = None
        # DDCB/FDCB系の場合は (DD, CB, ext) をキーにする
        if len(opcode) == 4 and opcode[0] in (0xDD, 0xFD) and opcode[1] == 0xCB:
            key = (opcode[0], opcode[1], opcode[3])
            u = self.cpu.op_map.get(key)
        else:
            # 2バイトキー検索
            if len(opcode) >= 2:
                key = tuple(opcode[:2])
                u = self.cpu.op_map.get(key)

            # 1バイトキー検索 (2バイトで見つからなかった場合)
            if u is None and len(opcode) >= 1:
                key = tuple(opcode[:1])
                u = self.cpu.op_map.get(key)

        if u is None:
            return None

        # ------------------------------------------------------
        # オペコードのバイト数で分岐
        # ------------------------------------------------------
        if u["bytes"] == len(opcode):
            handler = self._dispatch.get(u["bytes"])
            if handler:
                return handler(u, opcode, adr)
            return None
        else:
            return None

    def exec(self, start, images, size):
        """逆アセンブルを実行します。

        Args:
            start (int): 開始アドレス。
            images (list): バイナリイメージデータ (startアドレスからのデータ列)。
            size (int): データサイズ。

        Returns:
            list: 逆アセンブルされた行のリスト。
        """
        maxword = 0xFFFF
        mem = [0 for x in range(0x10000)]
        lst = []

        if size + start > maxword:
            return lst

        if (start > maxword) or (start < 0):
            return lst

        end = start + size - 1

        # イメージロード
        for p in range(size):
            mem[p + start] = images[p]

        adr = start
        lst.append({"address": adr, "asm": f"org 0x{adr:04X}"})
        # ------------------------------------------------------
        # 逆アセンブルを最長一致(4バイト)から順に試行
        # 結果は下記のフォーマットでリストへ保存
        #   [{"address":x = アドレス}, {"opcode":y = オペコード}, {"asm":z = アセンブル文字列}]
        # ------------------------------------------------------
        while adr <= end:
            p = None
            opcode = []

            # 最長の4バイトから順にマッチを試みる (Longest-match-first)
            if adr + 3 <= end:
                opcode_4b = mem[adr : adr + 4]
                p = self.op2asm(adr, opcode_4b)
                if p:
                    opcode = opcode_4b

            # 3バイトのマッチを試みる
            if p is None and adr + 2 <= end:
                opcode_3b = mem[adr : adr + 3]
                p = self.op2asm(adr, opcode_3b)
                if p:
                    opcode = opcode_3b

            # 2バイトのマッチを試みる
            if p is None and adr + 1 <= end:
                opcode_2b = mem[adr : adr + 2]
                p = self.op2asm(adr, opcode_2b)
                if p:
                    opcode = opcode_2b
            
            # 1バイトのマッチを試みる
            if p is None:
                opcode_1b = mem[adr : adr + 1]
                p = self.op2asm(adr, opcode_1b)
                if p:
                    opcode = opcode_1b

            if p is not None:
                lst.append({"address": adr, "opcode": opcode, "asm": p})
                adr += len(opcode)
            else:
                # どの長さでもマッチしなかった場合、1バイトのデータとして処理
                lst.append({"address": adr, "opcode": [mem[adr]], "asm": f"db 0x{mem[adr]:02X} ; Invalid Opcode"})
                adr += 1

        # ------------------------------------------------------
        # ラベル情報抽出
        # ------------------------------------------------------
        labels = {}
        for p in lst:
            m = re.search(r"(L_([0-9a-fA-F]{4}))", p["asm"])
            if m:
                target_addr = int(m.group(2), 16)
                labels[target_addr] = m.group(1) + ":"

        # アドレス検索用のマップを作成 (高速化)
        addr_map = {item["address"]: i for i, item in enumerate(lst)}

        for target_addr, label_str in labels.items():
            if target_addr in addr_map:
                lst[addr_map[target_addr]].update(label=label_str)

        return lst


def disassemble(data: bytes, start_address: int = 0) -> list[str]:
    """Z80バイナリデータを逆アセンブルしてアセンブリコードのリストを返します。

    Args:
        data (bytes): 逆アセンブル対象のバイナリデータ
        start_address (int, optional): 開始アドレス. Defaults to 0.

    Returns:
        list[str]: アセンブリコードの各行のリスト
    """
    d = Disasm()
    # execは (start, images, size) を受け取る
    # imagesはバイト列でOK
    result_data = d.exec(start_address, data, len(data))
    
    lines = []
    for p in result_data:
        label = p.get("label", "")
        if label:
            lines.append(label)
        
        asm_code = p.get("asm", "")
        if asm_code:
            # オペコードがある行（命令）はインデントする、ORGなどはインデントしない
            indent = "    " if p.get("opcode") else ""
            lines.append(f"{indent}{asm_code}")
            
    return lines
