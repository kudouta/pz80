#!/usr/bin/env python3

"""ニーモニック列からオペコードへの符号化。

`asm.py` の 4 責務のうち「符号化」を担う。トークン化済みの命令行を受け取り、
オペコード列と fixups（Pass 2 で埋めるラベル参照の位置情報）を返す。

外部から必要とするのは `Z80` インスタンスと、Pass 1 で確定する定義済みラベル集合
（`defined_labels`）だけ。ラベルのアドレス表そのものは持たず、`Asm` 側が保持する。

`Encoder` をクラスにしているのは、`cpu` を 10 個の関数へ引数で通し続けるより
1 箇所に保持する方が読みやすいため。可変な状態は `defined_labels` のみで、
これは Pass 1 が設定する。
"""

from pz80 import _evaluator
from pz80._util import current_address as _current_address
from pz80._util import format_location as _format_location


class Encoder:
    """トークン列をオペコードへ符号化する。

    Args:
        cpu (z80.Z80): 命令表・予約語を提供する CPU 定義。
    """

    def __init__(self, cpu):
        """Encoder を初期化します。"""
        self.cpu = cpu

        # Pass 1 が設定する定義済みラベル集合（$ やラベル参照の検証に使う）
        self.defined_labels = None

        # 既知のニーモニック・疑似命令セット (エラー診断用)
        self.known_mnemonics = {key[0] for key in self.cpu.asm_map}
        self.known_mnemonics |= {
            "org",
            "equ",
            "db",
            "defb",
            "dw",
            "defw",
            "ds",
            "defs",
            "end",
        }

    def asm2op(self, asmlist):
        """アセンブルリストからオペコードを生成
          1.数値はpythonでintで認識できるフォーマットであること
          2.数値は適応するオペコードで使用する桁(バイト/ワード)へ自動変換
          3.ラベルは事前検索で抽出済の文字が対象
          4.ラベルは自動変換で仮値(=0)をセットする

        Args:
            asmlist (dict): 1命令分のアセンブル情報。
                            例: {"line": 3, "asm": ["ld", "a", ",", "0x10"], "base": 0x100, "offset": 0}

        Returns:
            tuple: (opcode, fixups) の2要素タプル。
                opcode (list): 生成されたオペコードバイト列 (例: [0x3E, 0x10])
                fixups (list): Pass 2でアドレス解決が必要な情報のリスト。
                               各要素:
                                 "offset"(int) : opcode 内のバイト位置
                                 "size"(int)   : バイト数 (1 or 2)
                                 "type"(str)   : "byte", "word" , "rel"
                                 "src"(dict)   : _parse_operands() が返すオペランド辞書

        Raises:
            ValueError: 未知のニーモニック、またはオペランドの数や形が命令表に
                合わない場合。Pass 1 で未定義のシンボルも、このエラーで届く。
        """
        if asmlist.get("asm") is None:
            return [], []

        asm = asmlist["asm"]
        mnemonic = asm[0].lower()

        # 未知のニーモニックを早期検出 (_parse_operands が式評価を試みる前)
        if mnemonic not in self.known_mnemonics:
            raise ValueError(
                f"Unknown mnemonic '{asm[0]}' on {_format_location(asmlist['line'], asmlist.get('file'))}: {' '.join(asm)}"
            )

        # オペランドがオペコードに埋め込まれた literal の命令 (BIT/RES/SET/RST/IM 等)
        # は、トークン列そのままで asm_map に直接一致する。これを最初に試みる。
        direct = self.cpu.asm_map.get(tuple(t.lower() for t in asm))
        if direct is not None:
            return list(direct["code"]), []

        rs = self.parse_operands(asmlist)
        template_asm = list(asm)

        # DDCB/FDCB系の bit/res/set 命令 (例: bit 0, (ix+10)) のみ特別扱いが必要。
        # ビット番号は opcode に埋め込まれる literal のため operand から除外する。
        # 一方、変位 d (10 の部分) はプレースホルダー置換が必要なため _encode_byte_operand で処理する。
        if mnemonic in ("bit", "res", "set") and len(rs) > 0:
            rs.pop(0)

        # オペコード確定
        match len(rs):
            case 0:
                # 数値無し
                op, fixups = self._encode_no_operand(template_asm)

            case 1:
                # 1オペランド (バイト or ワード)
                op, fixups = self._encode_byte_operand(template_asm, rs[0])
                if len(op) == 0:
                    op, fixups = self._encode_word_operand(template_asm, rs[0])

            case 2:
                # 2オペランド (バイト x 2)
                op, fixups = self._encode_two_operands(template_asm, rs[0], rs[1])

            case _:
                raise ValueError(
                    f"Invalid operand count or format on {_format_location(asmlist['line'], asmlist.get('file'))}: {asmlist['asm']}"
                )

        return op, fixups

    def parse_operands(self, asmlist):
        """トークンリストからオペランドを抽出・解析する

        Args:
            asmlist (dict): 1命令分のアセンブル情報。
                            例: {"line": 3, "asm": ["ld", "a", ",", "0x10"], "base": 0x100, "offset": 0}

        Returns:
            list: オペランド情報辞書のリスト。
                  各要素:
                    "value"(int)    : 評価済み数値 (Pass 1では未定義ラベルは仮値 0)
                    "location"(int) : asm リスト内のトークン開始インデックス
                    "length":(int)  : 消費したトークン数
                    例: [{"value": 16, "location": 3, "length": 1}]  # "0x10" の場合
        """
        asm = asmlist["asm"]
        rs = []
        i = 0
        # DW, DBなどの疑似命令では、括弧で始まる複雑な式を単一オペランドとして扱う。
        # 通常の命令では、括弧はアドレッシングモードの一部とみなし、その内部を解析する。
        is_directive_with_expr = asm[0].lower() in ["dw", "defw", "db", "defb"]

        while i < len(asm):
            if not is_directive_with_expr and asm[i] == "(":
                i += 1
                continue

            starts_expr = self._is_expression_start(asm, i) or (
                is_directive_with_expr and asm[i] == "("
            )
            if not starts_expr:
                i += 1
                continue

            val, consumed = self._try_evaluate(asm, i, asmlist)
            if consumed == 0:
                i += 1
                continue

            rs.append(
                {
                    "value": val,
                    "location": i,
                    "length": consumed,
                    "line": asmlist["line"],
                    "file": asmlist.get("file"),
                }
            )
            i += consumed

        return rs

    def _try_evaluate(self, asm, index, asmlist):
        """Pass 1 の式評価を試みます。解析できなければ (None, 0) を返します。

        未定義シンボルだけは再送出します。Pass 1 での定義漏れは後段で拾えず、
        黙って無視すると誤ったオペコードになるためです。

        Args:
            asm (list): トークンリスト。
            index (int): 評価を開始するインデックス。
            asmlist (dict): 対象のアセンブル行（位置情報とベースアドレスに使う）。

        Returns:
            tuple[int | None, int]: (評価結果, 消費したトークン数)。

        Raises:
            ValueError: 未定義シンボル（`Undefined symbol`）の場合だけ。
        """
        try:
            return self.evaluate_expression(
                asm,
                index,
                None,  # pass1 用評価 (address_map=None)
                asmlist["line"],
                current_address=_current_address(asmlist),
                file_name=asmlist.get("file"),
            )
        except ValueError as e:
            if "Undefined symbol" in str(e):
                raise
            # 式の解析に失敗しても、ここでは無視して進む
            return None, 0

    def _is_expression_start(self, tokens, index):
        """指定されたインデックスのトークンが式の開始点となりうるかを判定します。

        Args:
            tokens (list): トークンリスト (例: ["ld", "a", ",", "0x10"])
            index (int): 判定対象トークンのインデックス。

        Returns:
            bool: 式の開始点となりうる場合はTrue。

        Note:
            '(' の扱いは呼び出し元で制御される。
            - 通常命令: '(' を事前にスキップするため、この関数には届かない。
            - 疑似命令: 呼び出し元の `or (is_directive_with_expr and asm[i] == '(')` が処理する。
            そのため、ここでは '(' を式の開始とはみなさない。
        """
        token = tokens[index]

        return not (
            # 予約語、区切り文字、特定の条件下の記号は式の開始点とはみなさない
            token.lower() in self.cpu.reserved
            # カンマや括弧類は式の開始点とはみなさない
            or token in [",", "(", ")", "]", "}"]
            # IX/IYレジスタの直後の +/- は変位指定であり、式の開始ではない
            or (
                token in ["+", "-"]
                and index > 0
                and tokens[index - 1].lower() in ("ix", "iy")
            )
        )

    def _find_expression_end(self, tokens, start_index):
        """式の開始インデックスから、式が終わるインデックスを見つけます。

        Args:
            tokens (list): トークンリスト (例: ["ld", "hl", ",", "LABEL", "+", "1"])
            start_index (int): 式の開始インデックス。

        Returns:
            int: 式が終わるインデックス（終端トークンの次の位置）。
                カンマまたは対応しない閉じ括弧の手前で停止する。
        """
        end_index = start_index
        paren_balance = 0
        while end_index < len(tokens):
            token = tokens[end_index]
            if token == "(":
                paren_balance += 1
            elif token == ")":
                paren_balance -= 1
            elif token == "," and paren_balance == 0:
                break
            if paren_balance < 0:
                break
            end_index += 1
        return end_index

    def evaluate_expression(
        self,
        tokens,
        start_index,
        address_map,
        line_num,
        current_address=None,
        file_name=None,
    ):
        """ExpressionEvaluatorを使用して、トークンリストから式を評価します。

        Args:
            tokens (list): トークンリスト (例: ["LABEL", "+", "1"])
            start_index (int): 評価を開始するインデックス。
            address_map (dict or None): ラベルとアドレスの対応辞書 (例: {"LABEL": 0x100})。Noneの場合はPass 1として扱い、ラベルには仮値 0 を返す。
            line_num (int): エラー報告用の行番号。
            current_address (int or None): $ として解決する現在のアドレス。
            file_name (str or None): エラー報告用のソース識別子。

        Returns:
            tuple: (value, consumed) の2要素タプル。
                value (int or None): 式の評価結果。トークンがない場合はNone。
                consumed (int): 消費したトークン数。
        """
        end_index = self._find_expression_end(tokens, start_index)

        expr_tokens = tokens[start_index:end_index]
        consumed = len(expr_tokens)

        if not expr_tokens:
            return None, 0

        # Pass 1では、address_mapはNoneです。検証のために定義済みラベルのセットを渡します。
        defined_labels_pass1 = self.defined_labels if address_map is None else None
        evaluator_instance = _evaluator.ExpressionEvaluator(
            expr_tokens,
            self.cpu.reserved,
            line_num,
            address_map,
            defined_labels_pass1,
            current_address=current_address,
            file_name=file_name,
        )
        value = evaluator_instance.evaluate()

        return value, consumed

    def _encode_no_operand(self, s):
        """オペランドなし命令の処理

        Args:
            s (list): トークンリスト (例: ["nop"])

        Returns:
            tuple: (opcode, fixups) の2要素タプル。
                opcode (list): オペコードバイト列 (例: [0x00])
                fixups (list): 常に空リスト []
        """
        ref = s[:]
        u = self._lookup_opcode(ref)
        return (u[0]["code"], []) if u else ([], [])

    def _encode_byte_operand(self, s, d):
        """オペコード1バイト

        Args:
            s (list): トークンリスト (例: ["ld", "a", ",", "0x{0}"])
            d (dict): _parse_operands() が返すオペランド情報辞書。
                {"value": int, "location": int, "length": int}
                例: {"value": 16, "location": 3, "length": 1}

        Returns:
            tuple: (opcode, fixups) の2要素タプル。
                opcode (list): オペコードバイト列 (例: [0x3E, 0x10])
                fixups (list): アドレス解決情報のリスト。
                               各要素:
                                 "offset"(int):
                                 "size"(int): 1
                                 "type"(str): "byte", "rel"
                                 "src"(dict):
        """
        # トークンリストからオペコードを検索
        # self._codetbl[]["asm"] と バイト(0x{0})の検索

        r = []
        fixups = []
        val = d["value"]
        ref = s[:]
        length = d.get("length", 1)
        # 数値または式の箇所をプレースホルダーに置換
        ref[d["location"] : d["location"] + length] = ["0x{0}"]
        p = self._lookup_opcode(ref)
        if not p:
            return r, fixups

        u = p[0]
        r = list(u["code"])

        # 相対アドレス対応
        if u.get("rel") is not None:
            r.append(0x00)  # プレースホルダー
            fixups.append({"offset": len(r) - 1, "size": 1, "type": "rel", "src": d})
            return r, fixups

        # 符号付き8bit (-128) ～ 符号なし8bit (255) の範囲を許容
        self.check_range(val, -128, 255, "Byte", d)

        val_byte = val & 0xFF

        # 0xddcb, 0xfdcb 対応
        e = u.get("ext")
        if e is not None:
            r.append(val_byte)  # プレースホルダーまたは値
            r.append(e)
            # DDCB/FDCBの変位dはオフセット2 (0:DD, 1:CB, 2:d, 3:ext)
            fixups.append({"offset": 2, "size": 1, "type": "byte", "src": d})
            return r, fixups

        r.append(val_byte)
        fixups.append({"offset": len(r) - 1, "size": 1, "type": "byte", "src": d})
        return r, fixups

    def _encode_word_operand(self, s, d):
        """オペコード2バイト

        Args:
            s (list): トークンリスト (例: ["ld", "hl", ",", "0x{1}{0}"])
            d (dict): _parse_operands() が返すオペランド情報辞書。
                {"value": int, "location": int, "length": int}
                例: {"value": 0x1234, "location": 3, "length": 1}

        Returns:
            tuple: (opcode, fixups) の2要素タプル。
                opcode (list): オペコードバイト列 (例: [0x21, 0x34, 0x12])
                fixups (list): アドレス解決情報のリスト。
                               各要素:
                                 "offset"(int) :
                                 "size"(int) : 2
                                 "type"(str) : "word"
                                 "src"(dict) :
        """
        # トークンリストからオペコードを検索
        # self._codetbl[]["asm"] と ワード(0x{1}{0})の検索

        r = []
        fixups = []
        val = d["value"]
        # 符号付き16bit (-32768) ～ 符号なし16bit (65535) の範囲を許容
        self.check_range(val, -32768, 65535, "Word", d)

        ref = s[:]
        length = d.get("length", 1)
        # 数値または式の箇所をプレースホルダーに置換
        ref[d["location"] : d["location"] + length] = ["0x{1}{0}"]
        u = self._lookup_opcode(ref)

        if not u:
            return r, fixups

        r = list(u[0]["code"])
        # 符号付き対応のためマスク処理
        val_word = val & 0xFFFF
        r.append(val_word & 0xFF)
        r.append((val_word >> 8) & 0xFF)

        fixups.append({"offset": len(r) - 2, "size": 2, "type": "word", "src": d})
        return r, fixups

    def _encode_two_operands(self, s, d0, d1):
        """オペコード3バイト

        Args:
            s (list): トークンリスト (例: ["ld", "(", "ix", "+", "0x{0}", ")", ",", "0x{1}"])
            d0 (dict): _parse_operands() が返す第1オペランド情報辞書。
                       各要素:
                         "value"(int):
                         "location"(int):
                         "length"(int):
                       例: {"value": 10, "location": 4, "length": 1}  # IX変位
            d1 (dict): _parse_operands() が返す第2オペランド情報辞書。
                       例: {"value": 5, "location": 7, "length": 1}   # 即値

        Returns:
            tuple: (opcode, fixups) の2要素タプル。
                opcode (list): オペコードバイト列 (例: [0xDD, 0x36, 0x0A, 0x05])
                fixups (list): アドレス解決情報のリスト (2要素)。
                               各要素:
                                 "offset"(int):
                                 "size"(int): 1
                                 "type"(str): "byte"
                                 "src"(dict):
        """
        # ld (ix/iy + d), n のような2つの数値オペランドを持つ命令を処理する

        r = []
        fixups = []
        val0 = d0["value"]
        val1 = d1["value"]

        self.check_range(val0, -128, 255, "Byte", d0)
        self.check_range(val1, -128, 255, "Byte", d1)

        ref = s[:]
        # d0の式トークンをプレースホルダーに置換（複数トークン式に対応するためスライス代入）
        loc0, len0 = d0["location"], d0.get("length", 1)
        ref[loc0 : loc0 + len0] = ["0x{0}"]
        # d0の置換でトークン列が縮んだ分だけ d1 のインデックスを補正
        loc1 = d1["location"] + (1 - len0)
        len1 = d1.get("length", 1)
        ref[loc1 : loc1 + len1] = ["0x{1}"]

        u = self._lookup_opcode(ref)
        if not u:
            return r, fixups
        r = list(u[0]["code"])
        r.extend([val0 & 0xFF, val1 & 0xFF])
        fixups.append({"offset": len(r) - 2, "size": 1, "type": "byte", "src": d0})
        fixups.append({"offset": len(r) - 1, "size": 1, "type": "byte", "src": d1})
        return r, fixups

    @staticmethod
    def check_range(value, low, high, kind, d):
        """オペランド値の範囲を検査します。

        Args:
            value (int): 検査する値。
            low (int): 下限（含む）。
            high (int): 上限（含む）。
            kind (str): エラーメッセージ中の種別名（"Byte" / "Word"）。
            d (dict): オペランド情報。位置情報（line / file）に使う。

        Raises:
            ValueError: 範囲外の場合。

        Note:
            `asm.py` の Pass 2 も同じ検査をするため、そちらからも呼ばれます
            （メッセージ形式を 1 箇所に保つため）。
        """
        if not (low <= value <= high):
            raise ValueError(
                f"{kind} value {value} out of range on "
                f"{_format_location(d['line'], d.get('file'))} "
                f"(expected {low} to {high})"
            )

    def _lookup_opcode(self, s):
        """オペコード検索

           小文字変換してCPUコードのリストから検索

        Args:
            s (list): プレースホルダー置換済みトークンリスト
                      例: ["ld", "a", ",", "0x{0}"]

        Returns:
            list: 一致した命令情報辞書を1要素持つリスト。不一致の場合は空リスト。
                  例: [{"code": [0x3E], "bytes": 2, "asm": ["ld", "a", ",", "0x{0}"]}]
        """
        key = tuple(y.lower() for y in s)
        item = self.cpu.asm_map.get(key)
        return [item] if item else []
