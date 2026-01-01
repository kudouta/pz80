#!/usr/bin/env python3

import re

from . import directives, evaluator, z80


class Asm:
    """Z80アセンブラクラス"""

    def __init__(self):
        """Asmクラスを初期化します。"""
        self.cpu = z80.Z80()
        self.directive_handler = directives.DirectiveHandler(self)
        self._reset()
        self._re_whitespace = re.compile(r"\s+")
        self._re_label_start = re.compile(r"^[A-Za-z@]+")
        # 文字列リテラル抽出用
        self._re_string_literal = re.compile(r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'")
        # プレースホルダー復元用
        self._re_placeholder = re.compile(r"__STRING_LITERAL_(\d+)__")

    def _reset(self):
        """アセンブル状態をリセットします。"""
        self.labelmap = []       # アセンブラソースから抽出したラベルリスト
        self.label2address = []  # ラベルに対応するアドレスリスト
        self.defined_labels = None

    def tokenize(self, src):
        """ソースコード1行をトークンリストに変換します。

           トークンリスト例 : ['ld', '(', 'hl', ')', ',', 'a'] ※コメント文字 ";" 以降の要素は削除する
           文字列リテラル "..." や '...' は1つのトークンとして扱う

        Args:
            src (str): ソース

        Returns:
            ['ld', '(', 'hl', ')', ',', 'a'] : トークンリスト
        """
        # 文字列リテラルを一時的にプレースホルダーに置き換える
        strings = []
        def string_replacer(match):
            strings.append(match.group(0))
            return f"__STRING_LITERAL_{len(strings)-1}__"

        # 文字列リテラルを保護してからコメントを除去
        # (文字列内のセミコロンがコメントとして扱われないようにするため)
        src_replaced = self._re_string_literal.sub(string_replacer, src)
        src_without_comment = src_replaced.split(';', 1)[0]

        # 指定記号(filters)前後にスペース挿入してトークンリスト生成
        filters = "():,+-*/" # ; はコメントとして処理済のため除外
        for p in filters:
            src_without_comment = src_without_comment.replace(p, " " + p + " ")

        bf = self._re_whitespace.split(src_without_comment)
        ws = [p for p in bf if (p != "")]

        # プレースホルダーを元の文字列リテラルに戻す
        final_tokens = []
        for token in ws:
            placeholder_match = self._re_placeholder.match(token)
            if placeholder_match:
                index = int(placeholder_match.group(1))
                final_tokens.append(strings[index])
            else:
                final_tokens.append(token)

        return final_tokens

    def source(self, asm):
        """アセンブラソースからアセンブルリストを生成

        Args:
            asm (list): アセンブラソース(複数行の文字列リスト)

        Returns:
            [{"line": 行番号, asm": トークンリスト}, ]: アセンブルリスト
        """
        src = []

        for p in range(len(asm)):
            # 1行をトークンに分ける
            lst = self.tokenize(asm[p])

            if lst:
                # 行番号とトークンをセット
                src.append({"line": p + 1, "asm": lst})

        return src

    def equ(self, asm):
        """EQUラベルを全て数値へ置換

        Args:
            asm (list): アセンブルリスト
        """
        # EQU総当たり置換
        for m in self.labelmap:
            if m.get("type") == "equ":
                for q in asm:
                    if "asm" in q:
                        for r in range(len(q["asm"])):
                            if m["symbol"] == q["asm"][r]:
                                q["asm"][r] = m["value"]

    def opcode(self, s):
        """オペコード検索

           小文字変換してCPUコードのリストから検索

        Args:
            s (list): アセンブルリスト

        Returns:
            list: アセンブルリストと一致するCPUコードテーブル値
        """
        key = tuple([y.lower() for y in s])
        item = self.cpu.asm_map.get(key)
        return [item] if item else []

    def op0(self, s):
        """オペランドなし命令の処理

        Args:
            s (list): アセンブルリスト

        Returns:
            tuple: (オペコードリスト, fixupリスト)
        """
        ref = s[:]
        u = self.opcode(ref)
        return (u[0]["code"], []) if u else ([], [])

    def op1(self, s, d):
        """オペコード1バイト

        Args:
            s (list): アセンブルリスト
            d (list): label()で生成したリスト

        Returns:
            tuple: (オペコードリスト, fixupリスト)
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
        p = self.opcode(ref)
        if not p:
            return r, fixups

        u = p[0]
        r = list(u["code"])

        # 相対アドレス対応
        if u.get("rel") is not None:
            r.append(0x00) # プレースホルダー
            fixups.append({"offset": len(r) - 1, "size": 1, "type": "rel", "src": d})
            return r, fixups

        # 符号付き8bit (-128) ～ 符号なし8bit (255) の範囲を許容
        if not (-128 <= val <= 255):
            raise ValueError(f"Byte value out of range: {val}")

        val_byte = val & 0xFF

        # 0xddcb, 0xfdcb 対応
        e = u.get("ext")
        if e is not None:
            r.append(val_byte) # プレースホルダーまたは値
            r.append(e)
            # DDCB/FDCBの変位dはオフセット2 (0:DD, 1:CB, 2:d, 3:ext)
            fixups.append({"offset": 2, "size": 1, "type": "byte", "src": d})
            return r, fixups

        r.append(val_byte)
        fixups.append({"offset": len(r) - 1, "size": 1, "type": "byte", "src": d})
        return r, fixups

    def op2(self, s, d):
        """オペコード2バイト

        Args:
            s (list): アセンブルリスト
            d (list): label()で生成したリスト

        Returns:
            tuple: (オペコードリスト, fixupリスト)
        """
        # トークンリストからオペコードを検索
        # self._codetbl[]["asm"] と ワード(0x{1}{0})の検索

        r = []
        fixups = []
        val = d["value"]
        # 符号付き16bit (-32768) ～ 符号なし16bit (65535) の範囲を許容
        if not (-32768 <= val <= 65535):
            raise ValueError(f"Word value out of range: {val}")

        ref = s[:]
        length = d.get("length", 1)
        # 数値または式の箇所をプレースホルダーに置換
        ref[d["location"] : d["location"] + length] = ["0x{1}{0}"]
        u = self.opcode(ref)
        
        if not u:
            return r, fixups

        r = list(u[0]["code"])
        # 符号付き対応のためマスク処理
        val_word = val & 0xFFFF
        r.append(val_word & 0xFF)
        r.append((val_word >> 8) & 0xFF)
        
        fixups.append({"offset": len(r) - 2, "size": 2, "type": "word", "src": d})
        return r, fixups

    def op3(self, s, d0, d1):
        """オペコード3バイト

        Args:
            s (list): アセンブルリスト
            d0 (list): label()で生成したリスト
            d1 (list): label()で生成したリスト

        Returns:
            tuple: (オペコードリスト, fixupリスト)
        """
        # ld (ix/iy + d), n のような2つの数値オペランドを持つ命令を処理する

        r = []
        fixups = []
        val0 = d0["value"]
        val1 = d1["value"]

        if not (-128 <= val0 <= 255) or not (-128 <= val1 <= 255):
            raise ValueError(f"Operand value out of range: {val0}, {val1}")

        ref = s[:]
        ref[d0["location"]] = "0x{0}"  # 数値の箇所をバイト指定文字列に置換
        ref[d1["location"]] = "0x{1}"  # 数値の箇所をバイト指定文字列に置換

        u = self.opcode(ref)
        if not u:
            return r, fixups
        r = list(u[0]["code"])
        r.extend([val0 & 0xFF, val1 & 0xFF])
        fixups.append({"offset": len(r) - 2, "size": 1, "type": "byte", "src": d0})
        fixups.append({"offset": len(r) - 1, "size": 1, "type": "byte", "src": d1})
        return r, fixups

    def _is_expression_start(self, tokens, index):
        """指定されたインデックスのトークンが式の開始点となりうるかを判定します。"""
        token = tokens[index]

        # 予約語は式の開始点ではない
        if token.lower() in self.cpu.reserved:
            return False

        # カンマや閉じ括弧は式の開始点ではない
        if token in [',', ')', ']', '}']:
            return False

        # IX/IYレジスタの直後の +/- は区切り文字であり、式の開始ではない
        if token in ['+', '-'] and index > 0 and tokens[index - 1].lower() in ('ix', 'iy'):
            return False

        # (HL) のようなアドレス参照の開き括弧は式の開始点ではない
        if token == '(' and index + 2 < len(tokens) and tokens[index+2] == ')' and tokens[index+1].lower() in self.cpu.reserved:
            return False

        return True

    def _find_expression_end(self, tokens, start_index):
        """式の開始インデックスから、式が終わるインデックスを見つけます。"""
        end_index = start_index
        paren_balance = 0
        while end_index < len(tokens):
            token = tokens[end_index]
            if token == '(':
                paren_balance += 1
            elif token == ')':
                paren_balance -= 1
            elif token == ',' and paren_balance == 0:
                break
            if paren_balance < 0:
                break
            end_index += 1
        return end_index

    def _parse_operands(self, asmlist):
        """トークンリストからオペランドを抽出・解析する"""
        asm = asmlist["asm"]
        rs = []
        i = 0
        is_directive_with_expr = asm[0].lower() in ['dw', 'defw', 'db', 'defb']

        while i < len(asm):
            # DW, DBなどの疑似命令では、括弧で始まる複雑な式を単一オペランドとして扱う。
            # 通常の命令では、括弧はアドレッシングモードの一部とみなし、その内部を解析する。
            if not is_directive_with_expr and asm[i] == '(':
                i += 1
                continue

            if self._is_expression_start(asm, i) or (is_directive_with_expr and asm[i] == '('):
                # pass1用評価 (label_map=None)
                try:
                    val, consumed = self._evaluate_expression(asm, i, None, asmlist['line'])
                except ValueError as e:
                    # 未定義シンボルエラーの場合は再送出する (pass1でのエラー報告のため)
                    if "Undefined symbol" in str(e):
                        raise e
                    # 式の解析に失敗しても、ここでは無視して進む
                    val, consumed = None, 0
                if consumed > 0:
                    rs.append({"value": val, "location": i, "length": consumed})
                    i += consumed
                    continue
            
            i += 1
        return rs

    def asm2op(self, asmlist):
        """アセンブルリストからオペコードを生成

          1.数値はpythonでintで認識できるフォーマットであること

          2.数値は適応するオペコードで使用する桁(バイト/ワード)へ自動変換

          3.ラベルは事前検索で抽出済の文字が対象

          4.ラベルは自動変換で仮値(=0)をセットする

        Args:
            asmlist (list): アセンブルリスト

        Returns:
            tuple : (オペコードリスト, fixupリスト)
        """
        if asmlist.get("asm") is None:
            return [], []

        asm = asmlist["asm"]

        rs = self._parse_operands(asmlist)

        # オペコード検索用のテンプレートを作成
        # bit/res/set や条件付きジャンプなど、オペランドがニーモニックに含まれるケースに対応
        template_asm = list(asm)
        mnemonic = asm[0].lower()

        if mnemonic in ["bit", "res", "set"]:
            if len(rs) > 0:
                rs.pop(0)

        # オペコード確定
        match(len(rs)):
            case 0:
                # 数値無し
                op, fixups = self.op0(template_asm)

            case 1:
                # 1オペランド (バイト or ワード)
                op, fixups = self.op1(template_asm, rs[0])
                if len(op) == 0:
                    op, fixups = self.op2(template_asm, rs[0])

            case 2:
                # 2オペランド (バイト x 2)
                op, fixups = self.op3(template_asm, rs[0], rs[1])

            case _:
                raise ValueError(f"Invalid operand count or format in line {asmlist['line']}: {asmlist['asm']}")

        return op, fixups

    def pass0(self, src):
        """アセンブル事前準備

        Args:
            src (list): アセンブルリスト

        Returns:
            list : アセンブル処理のためのリスト

            [{"line": line, "asm": asm, "base": start, "offset": 0},]

            または

            [{"line": line, "label": asm[0], "base": start, "offset": 0},]
        """
        len_org   = 2
        len_equ   = 4
        len_label = 2
        result = []
        start = -1
        defined_symbols = set()
        for entry in src:
            asm = entry["asm"]
            line = entry["line"]
            maxword = 0xFFFF
            ope     = None

            # 種別判定
            if (len(asm) == len_org) and (asm[0].lower() == "org"):
                # ORG行
                ope = "org"
                try:
                    start = int(asm[1], 0)

                except ValueError:
                    raise ValueError(f"Invalid address format for ORG in line {line}: {asm[1]}")

            if (len(asm) == len_equ) and (asm[1] == ":") and (asm[2].lower() == "equ"):
                # EQU行
                ope = "equ"

            elif (len(asm) == len_label) and (asm[1] == ":"):
                # ラベルのみ
                ope = "label"

            elif (len(asm) > len_label) and (asm[1] == ":"):
                # ラベル + オペコード
                ope = "label+opcode"

            if ope is None:
                result.append({"line": line, "asm": asm, "base": start, "offset": 0})
                continue

            # 予約語チェック
            if list(filter(lambda x: x == asm[0].lower(), self.cpu.reserved)):
                raise ValueError(f"Invalid label '{asm[0]}' in line {line}: Reserved word")

            # 重複チェック
            if asm[0] in defined_symbols:
                raise ValueError(f"Duplicate label definition '{asm[0]}' in line {line}")

            # 先頭文字チェック
            if self._re_label_start.search(asm[0]) is None:
                raise ValueError(f"Invalid label format '{asm[0]}' in line {line}")

            # 数値チェック(equのみ)
            if ope == "equ":
                try:
                    val = int(asm[3], 0)
                    if (val >= maxword) or (val < 0):
                        raise ValueError(f"EQU value out of range (0-65535) in line {line}: {asm[0]} = {asm[3]}")

                except ValueError:
                    raise ValueError(f"Invalid value format for EQU in line {line}: {asm[3]}")

            # type, symbol, value形式でリスト登録
            match ope:
                case "equ":
                    self.labelmap.append({"type": ope, "symbol": asm[0], "value": asm[3]})
                    defined_symbols.add(asm[0])

                case "label":
                    self.labelmap.append({"type": ope, "symbol": asm[0], "value": 0})
                    defined_symbols.add(asm[0])
                    result.append({"line": line, "label": asm[0], "base": start, "offset": 0})

                case "label+opcode":
                    # ラベルとオペコードを2行へ分離
                    self.labelmap.append({"type": ope, "symbol": asm[0], "value": 0})
                    defined_symbols.add(asm[0])
                    u = asm[:]
                    result.append({"line": line, "label": asm[0], "base": start, "offset": 0})
                    del u[0:2]
                    result.append({"line": line, "asm": u, "base": start, "offset": 0})

                case _:
                    pass

        return result

    def pass1(self, asm):
        """アセンブル処理 その1

        Args:
            list : アセンブル処理のためのリスト
            [{"line": line, "asm": asm, "base": start, "offset": 0, "opcode": [n, n, n,]},]

            または

            [{"line": line, "label": asm[0], "base": start, "offset": 0},]

        """
        current_offset = 0
        last_base = None
        
        # 高速化のために定義済みラベルのセットを事前に作成
        self.defined_labels = {m["symbol"] for m in self.labelmap}

        try:
            for item in asm:
                # ベースアドレスが変わった場合 (ORGなど)
                if item["base"] != last_base:
                    current_offset = 0
                    last_base = item["base"]

                # asm行
                if item.get("asm") is not None:
                    mnemonic = item["asm"][0].lower()
                    # DB/DEFB 疑似命令の処理
                    if mnemonic in ["db", "defb"]:
                        opcodes = self.directive_handler.process_db_pass1(item)
                        item.update({"base": last_base, "offset": current_offset, "opcode": opcodes})
                        current_offset += len(opcodes)
                        continue  # 通常のオペコード処理をスキップ

                    # DW/DEFW 疑似命令の処理
                    elif mnemonic in ["dw", "defw"]:
                        opcodes = self.directive_handler.process_dw_pass1(item)
                        item.update({"base": last_base, "offset": current_offset, "opcode": opcodes})
                        current_offset += len(opcodes)
                        continue

                    # アセンブル
                    op, fixups = self.asm2op(item)
                    if not op:
                        raise ValueError(f"Invalid instruction or syntax in line {item['line']}: {' '.join(item['asm'])}")

                    # アドレス更新
                    item.update({"base": last_base, "offset": current_offset, "opcode": op, "fixups": fixups})
                    current_offset += len(op)

                # label行
                elif item.get("label") is not None:
                    # アドレス確定
                    adr = last_base + current_offset
                    # ラベル - アドレステーブル更新
                    self.label2address.append({"label": item["label"], "address": adr})
                    # ラベルのオフセット更新
                    item.update({"offset": current_offset})
        finally:
            self.defined_labels = None

    def _evaluate_expression(self, tokens, start_index, label_map, line_num):
        """
        ExpressionEvaluatorを使用して、トークンリストから式を評価します。
        """
        end_index = self._find_expression_end(tokens, start_index)
        
        expr_tokens = tokens[start_index:end_index]
        consumed = len(expr_tokens)

        if not expr_tokens:
            return None, 0

        # Pass 1では、label_mapはNoneです。検証のために定義済みラベルのセットを渡します。
        defined_labels_pass1 = self.defined_labels if label_map is None else None
        evaluator_instance = evaluator.ExpressionEvaluator(expr_tokens, label_map, line_num, self.cpu, defined_labels_pass1)
        value = evaluator_instance.evaluate()
        
        return value, consumed

    def _pass2_instruction(self, p, label_map):
        """通常命令のアドレス解決"""
        fixups = p.get("fixups", [])
        if not fixups:
            return

        opcode = p["opcode"]

        for fixup in fixups:
            src = fixup["src"]
            # Pass1で特定した位置から式を再評価
            address, consumed = self._evaluate_expression(p["asm"], src["location"], label_map, p["line"])
            
            if address is None:
                # 式が解決できない場合はスキップ（あるいはエラー）
                continue

            if fixup["type"] == "rel":
                # 相対ジャンプ
                pc = p["base"] + p["offset"] + len(opcode)
                offset = address - pc
                if not (-128 <= offset <= 127):
                    raise ValueError(f"Relative jump out of range ({offset}) in line {p['line']}")
                if offset < 0:
                    offset += 0x100
                opcode[fixup["offset"]] = offset & 0xFF

            elif fixup["type"] == "word":
                # 16bit値
                if not (-32768 <= address <= 65535):
                    raise ValueError(f"Word value out of range: {address} in line {p['line']}")
                opcode[fixup["offset"]] = address & 0xFF
                opcode[fixup["offset"] + 1] = (address >> 8) & 0xFF

            elif fixup["type"] == "byte":
                # 8bit値
                if not (-128 <= address <= 255):
                    raise ValueError(f"Byte value out of range: {address} in line {p['line']}")
                opcode[fixup["offset"]] = address & 0xFF

    def pass2(self, asm):
        """アセンブル処理 その2

        Args:
            list : アセンブル処理のためのリスト
            [{"line": line, "asm": asm, "base": start, "offset": 0, "opcode": [n, n, n,]},]
        """
        # ラベルマップの作成 (リストから辞書へ変換して高速化)
        label_map = {item["label"]: item["address"] for item in self.label2address}
        
        # self.labelmap の値を更新 (asm2op で参照されるため)
        for m in self.labelmap:
            if m["symbol"] in label_map:
                m["value"] = label_map[m["symbol"]]

        for p in asm:
            if "asm" not in p:
                continue

            mnemonic = p["asm"][0].lower()
            
            # DW/DEFW 疑似命令の処理
            if mnemonic in ["dw", "defw"]:
                self.directive_handler.process_dw_pass2(p, label_map)
                continue
            # 通常命令
            self._pass2_instruction(p, label_map)

    def assemble_lines(self, lines):
        """行リストからアセンブルを実行します。

        Args:
            lines (list): ソースコードの行リスト

        Returns:
            list : アセンブル処理のためのリスト
            [{"line": line, "asm": asm, "base": start, "offset": 0, "opcode": [n, n, n,]},]
        """
        # 状態リセット
        self._reset()

        # ------------------------------------------------------
        # アセンブルリスト src を生成
        #   フォーマットは下記の通り
        #      [{"line": 行番号, "asm": トークンリスト}, ]
        # ------------------------------------------------------
        src = self.source(lines)

        # ------------------------------------------------------
        # src から
        # (1) ラベルリスト生成
        # (2) ORGがあればべースアドレスを再定義
        # (3) アセンブルリスト asm を生成
        # ----- -------------------------------------------------
        asm = self.pass0(src)

        # ------------------------------------------------------
        # EQUを数値へ置換
        # ------------------------------------------------------
        self.equ(asm)

        # ------------------------------------------------------
        # アセンブル(pass1)
        #
        # (1) アドレスを確定してアセンブルリストを更新
        #     フォーマットは下記2通り
        #     [1]
        #       [
        #          {"line": 行番号,
        #           "asm": [アセンブルリスト],
        #           "base": ORG起点のベースアドレス,
        #           "offset": baseオフセット,
        #           "opcode": [オペコード]},
        #       ]
        #
        #     [2]
        #       [
        #          {"line": 行番号,
        #           "label": ラベル,
        #           "base": ORG起点のベースアドレス,
        #           "offset": baseオフセット,
        #       ]
        # (2) ラベル - アドレステーブルを生成
        #       フォーマットは以下の通り
        #       [{"label": ラベル, "address": アドレス},]
        #
        # ------------------------------------------------------
        self.pass1(asm)
        
        # ------------------------------------------------------
        # アセンブル(pass2)
        # (1) 仕上げ...ラベルを使っているオペランドのアドレスを解決
        # ------------------------------------------------------
        self.pass2(asm)
            
        return asm

    def exec(self, name):
        """アセンブル処理メイン (ファイル入力)

        Args:
            name (str): ソースファイル名

        Returns:
            list : アセンブル処理のためのリスト
        """
        try:
            with open(name, encoding="utf-8") as f:
                fs = f.readlines()
        except FileNotFoundError:
            raise FileNotFoundError(f"Source file not found: {name}")

        return self.assemble_lines(fs)


def assemble(source: str) -> bytes:
    """Z80ソースコードをアセンブルしてバイナリデータを返します。

    Args:
        source (str): アセンブリソースコード

    Returns:
        bytes: アセンブルされたバイナリデータ
    """
    assembler = Asm()
    lines = source.splitlines()
    asm_result = assembler.assemble_lines(lines)

    # メモリイメージの構築 (64KB空間)
    # 簡易実装として、出力されるデータが存在する最小アドレスから最大アドレスまでを返す
    memory = {}
    min_addr = 0xFFFF
    max_addr = 0

    for line in asm_result:
        if "opcode" in line and line["opcode"]:
            base = line.get("base", 0)
            offset = line.get("offset", 0)
            addr = base + offset
            opcode = line["opcode"]
            
            for i, byte in enumerate(opcode):
                current_addr = addr + i
                memory[current_addr] = byte
                if current_addr < min_addr:
                    min_addr = current_addr
                if current_addr > max_addr:
                    max_addr = current_addr

    if not memory:
        return b""

    # 最小アドレスから最大アドレスまでのバイト列を生成 (隙間は0x00埋め)
    size = max_addr - min_addr + 1
    result = bytearray(size)
    
    for addr, byte in memory.items():
        result[addr - min_addr] = byte

    return bytes(result)
