#!/usr/bin/env python3

import ast

from pz80 import _evaluator
from pz80._util import current_address as _current_address
from pz80._util import format_location as _format_location


class DirectiveHandler:
    """
    アセンブラの疑似命令（Directives）の処理を専門に扱うクラス。
    """

    def __init__(self, asm_instance):
        """
        ハンドラを初期化します。

        Args:
            asm_instance (Asm): 呼び出し元のAsmクラスのインスタンス。
        """
        self.asm = asm_instance

    def process_db_pass1(self, item):
        """DB/DEFB 疑似命令のオペランドを解析します (Pass 1)。

        Args:
            item (dict): アセンブルリストの1行分の辞書。
                         各要素:
                           "line"(int):
                           "file"(str|None):
                           "asm"([str, ...]):
                           "base"(int):
                           "offset"(int):
                         例: {"line": 5, "asm": ["db", "0x41", ",", "0x42"], "base": 0x100, "offset": 0}

        Returns:
            list: 解析されたバイト値のリスト。ラベルや式はプレースホルダー 0x00。
                例: [0x41, 0x42]

        Raises:
            ValueError: 文字列リテラルとして読めない、式として読めない、または
                値が 0〜255 に収まらない場合。
        """
        opcodes = []
        loc = _format_location(item["line"], item.get("file"))

        for operand in self._split_operands(item["asm"][1:]):
            token = operand[0]
            if len(operand) == 1 and token[0] in ('"', "'"):
                try:
                    decoded_string = ast.literal_eval(token)
                    opcodes.extend(ord(c) for c in decoded_string)
                except (ValueError, SyntaxError) as e:
                    raise ValueError(f"Invalid string literal on {loc}: {token}") from e
            else:
                value = self._eval_pass1_operand(
                    item, operand, f"Invalid operand for DB on {loc}: {token}"
                )
                if value is None or not (0 <= value <= 255):
                    raise ValueError(
                        f"DB value out of byte range (0-255) on {loc}: {value}"
                    )
                opcodes.append(value)
        return opcodes

    def process_db_pass2(self, p, address_map):
        """DB/DEFB 疑似命令のアドレス解決を行います (Pass 2)。

        Args:
            p (dict): _pass1() 処理済みのアセンブルリスト1行分の辞書。
                         各要素:
                           "line"(int):
                           "file"(str|None):
                           "asm"([str, ...]):
                           "base"(int):
                           "offset"(int):
                           "opcode"([int, ...]):
                         例: {"line": 5, "asm": ["db", "LABEL"], "base": 0x100, "offset": 4, "opcode": [0x00]}
            address_map (dict): ラベルとアドレスの対応辞書。
                例: {"LABEL": 0x0042}

        Raises:
            ValueError: 文字列リテラルとして読めない場合。式の値の検査
                （`_eval_pass2_operand()`）が出すエラーもそのまま届く。
        """
        current_byte_offset = 0
        loc = _format_location(p["line"], p.get("file"))

        # p["asm"]の先頭は'DB'なのでスキップ
        i = 1
        while i < len(p["asm"]):
            token = p["asm"][i]

            if token == ",":
                i += 1
                continue

            if token[0] in ('"', "'"):
                # 文字列リテラル: Pass 1で確定済み。バイト数だけオフセットを進める。
                try:
                    decoded_string = ast.literal_eval(token)
                except (ValueError, SyntaxError) as e:
                    raise ValueError(f"Invalid string literal on {loc}: {token}") from e
                current_byte_offset += len(decoded_string)
                i += 1
            else:
                value, consumed = self._eval_pass2_operand(
                    p, i, address_map, "DB", 255, "byte"
                )
                p["opcode"][current_byte_offset] = value & 0xFF
                current_byte_offset += 1
                i += consumed

    def process_dw_pass1(self, item):
        """DW/DEFW 疑似命令のオペランドを解析します (Pass 1)。

        Args:
            item (dict): アセンブルリストの1行分の辞書。
                         各要素:
                           "line"(int):
                           "file"(str|None):
                           "asm"([str, ...]):
                           "base"(int):
                           "offset"(int):
                         例: {"line": 6, "asm": ["dw", "0x1234", ",", "LABEL"], "base": 0x100, "offset": 0}

        Returns:
            list: 解析されたバイト値のリスト（各ワード2バイト、ラベルや式はプレースホルダー0x00）。
                例: [0x34, 0x12, 0x00, 0x00]  # 0x1234 はリトルエンディアン、LABEL は仮値
        """
        opcodes = []
        operands_list = self._split_operands(item["asm"][1:])

        for operand_tokens in operands_list:
            # オペランドが単一トークンかどうかで処理を分岐
            if len(operand_tokens) == 1:
                token = operand_tokens[0]
                try:
                    value = ast.literal_eval(token)
                except (ValueError, SyntaxError):
                    pass
                else:
                    opcodes.extend(
                        self._encode_dw_literal(
                            value, token, item["line"], item.get("file")
                        )
                    )
                    continue

            # ラベルまたは式として扱う (プレースホルダーを挿入)
            opcodes.extend([0x00, 0x00])

        return opcodes

    def process_dw_pass2(self, p, address_map):
        """DW/DEFW 疑似命令のアドレス解決を行います (Pass 2)。

        Args:
            p (dict): _pass1() 処理済みのアセンブルリスト1行分の辞書。
                         各要素:
                           "line"(int):
                           "file"(str|None):
                           "asm"([str, ...]):
                           "base"(int):
                           "offset"(int):
                           "opcode"([int, ...]):
                         例: {"line": 6, "asm": ["dw", "LABEL"], "base": 0x100, "offset": 4, "opcode": [0x00, 0x00]}
            address_map (dict): ラベルとアドレスの対応辞書。
                例: {"LABEL": 0x0200, "SUB": 0x0300}
        """
        current_byte_offset = 0

        # p["asm"]の先頭は'DW'なのでスキップ
        i = 1
        while i < len(p["asm"]):
            token = p["asm"][i]

            if token == ",":
                i += 1
                continue

            address, consumed = self._eval_pass2_operand(
                p, i, address_map, "DW", 65535, "word"
            )
            p["opcode"][current_byte_offset] = address & 0xFF
            p["opcode"][current_byte_offset + 1] = (address >> 8) & 0xFF

            current_byte_offset += 2
            i += consumed

    def process_ds_pass1(self, item):
        """DS/DEFS 疑似命令のオペランドを解析します (Pass 1)。

        Args:
            item (dict): アセンブルリストの1行分の辞書。
                         例: {"line": 7, "asm": ["ds", "0x10"], "base": 0x100, "offset": 0}

        Returns:
            list: count バイト分の 0x00 リスト（fill 指定時はその値）。

        Raises:
            ValueError: オペランドが無い、count や fill にラベルを書いた、count が
                負、または fill が 0〜255 に収まらない場合。
        """
        loc = _format_location(item["line"], item.get("file"))
        operands = self._split_operands(item["asm"][1:])
        if not operands:
            raise ValueError(f"DS requires at least one operand on {loc}")

        self._reject_label_operand(operands[0], "DS", "count", loc)
        count = self._eval_pass1_operand(
            item, operands[0], f"Invalid count expression for DS on {loc}"
        )
        if count is None or count < 0:
            raise ValueError(
                f"DS count must be a non-negative integer on {loc}: {count}"
            )

        fill = 0
        if len(operands) >= 2:
            self._reject_label_operand(operands[1], "DS", "fill", loc)
            fill = self._eval_pass1_operand(
                item, operands[1], f"Invalid fill expression for DS on {loc}"
            )
            if fill is None or not (0 <= fill <= 255):
                raise ValueError(
                    f"DS fill value out of byte range (0-255) on {loc}: {fill}"
                )

        return [fill] * count

    def process_align_pass1(self, item):
        """ALIGN 疑似命令のオペランドを解析します (Pass 1)。

        現在アドレスから次の境界までを fill で埋めます。既に境界上なら 0 バイトで、
        `ALIGN` 行そのものは残りますが何も出力しません。

        境界は**アドレスの絶対値**に対して取ります（`ORG` からの相対ではありません）。
        `$` と同じ基準なので、`ds` で手書きしていた `ds 16 - ($ & 0x0F)` と同じ結果に
        なります。

        境界は **2 の冪** に限ります。`ALIGN 10` を `0x10` の打ち間違いとして弾くためで、
        `ds` イディオムのビットマスクが元々 2 の冪しか扱えなかったことにも合わせています。

        Args:
            item (dict): アセンブルリストの1行分の辞書。
                         例: {"line": 7, "asm": ["align", "0x10"], "base": 0x100, "offset": 3}

        Returns:
            list: 詰めるバイト数分の 0x00 リスト（fill 指定時はその値）。

        Raises:
            ValueError: 境界が 2 の冪でない、正でない、fill が範囲外の場合。
        """
        loc = _format_location(item["line"], item.get("file"))
        operands = self._split_operands(item["asm"][1:])
        if not operands:
            raise ValueError(f"ALIGN requires at least one operand on {loc}")

        self._reject_label_operand(operands[0], "ALIGN", "boundary", loc)
        boundary = self._eval_pass1_operand(
            item, operands[0], f"Invalid boundary expression for ALIGN on {loc}"
        )
        if boundary is None or boundary < 1:
            raise ValueError(
                f"ALIGN boundary must be a positive integer on {loc}: {boundary}"
            )
        # 2 の冪は「下位ビットが立っていない」で判定できる（8 & 7 == 0）。
        if boundary & (boundary - 1):
            raise ValueError(
                f"ALIGN boundary must be a power of 2 on {loc}: {boundary}"
            )

        fill = 0
        if len(operands) >= 2:
            self._reject_label_operand(operands[1], "ALIGN", "fill", loc)
            fill = self._eval_pass1_operand(
                item, operands[1], f"Invalid fill expression for ALIGN on {loc}"
            )
            if fill is None or not (0 <= fill <= 255):
                raise ValueError(
                    f"ALIGN fill value out of byte range (0-255) on {loc}: {fill}"
                )

        # 外側の % は「既に境界上」を 0 に畳むためのもの。
        # これが無いと境界ちょうどのときに boundary バイト分を余計に詰める。
        address = _current_address(item)
        padding = (boundary - address % boundary) % boundary
        return [fill] * padding

    def _reject_label_operand(self, tokens, kind, what, loc):
        """Pass 1 で値が確定していなければならないオペランドを検査します。

        `DS` / `ALIGN` は出すバイト数がそれ以降のアドレスをすべて動かすので、
        値が Pass 1 で決まっていなければなりません。ラベルのアドレスはこの時点では
        未確定で、シンボル表には仮の `0` が入っています。素通しすると式が黙って
        `0` として評価され、`ds LATER` が**エラーも警告も出さずに 0 バイト**を
        出してしまいます。アセンブルは成功するので気づけません。

        `DB` / `DW` は Pass 2 で評価し直すため、この制限を受けません。
        ラベルを書けるかどうかが疑似命令ごとに違うのは、値が要る時期の違いによります。

        Args:
            tokens (list): 検査するオペランドのトークン列。
            kind (str): メッセージ中の疑似命令名（"DS" / "ALIGN"）。
            what (str): メッセージ中のオペランド名（"count" / "boundary" / "fill"）。
            loc (str): エラーメッセージ用の位置情報。

        Raises:
            ValueError: ラベルを参照している場合。
        """
        for token in tokens:
            if self.asm.symbols.is_label(token):
                raise ValueError(
                    f"{kind} {what} cannot reference a label on {loc}: {token} "
                    f"(label addresses are not resolved in pass 1)"
                )

    def _eval_pass1_operand(self, item, tokens, error_message):
        """Pass 1 でオペランド 1 個を評価します。

        Pass 1 の評価文脈（ラベル表は未確定なので `address_map=None`、代わりに
        定義済みラベル名の集合と現在アドレスを渡す）は DB / DS のどこでも同じで、
        違うのは評価するトークン列と失敗時の文言だけなので、ここに集約します。

        Args:
            item (dict): 対象のアセンブル行（位置情報と現在アドレスに使う）。
            tokens (list): 評価するトークン列。
            error_message (str): 評価に失敗したときに送出するメッセージ。

        Returns:
            int | None: 評価結果。未解決なら None。

        Raises:
            ValueError: 式が評価できない場合（`error_message` を添えて再送出）。
        """
        try:
            ev = _evaluator.ExpressionEvaluator(
                tokens,
                self.asm.cpu.reserved,
                item["line"],
                address_map=None,
                # 未定義シンボルの判定に使うシンボル表（`in` で引ける）。
                defined_labels_pass1=self.asm.symbols,
                current_address=_current_address(item),
                file_name=item.get("file"),
            )
            return ev.evaluate()
        except ValueError as e:
            raise ValueError(error_message) from e

    def _eval_pass2_operand(self, p, index, address_map, kind, high, unit):
        """Pass 2 でオペランド 1 個を評価し、範囲検査して返します。

        DB / DW の Pass 2 は「式評価 → 未解決チェック → 範囲チェック」が共通です。
        違うのは許容範囲とメッセージ中の語だけなので、ここに集約します。

        Args:
            p (dict): 対象のアセンブル行。
            index (int): 評価を開始するトークンインデックス。
            address_map (dict): ラベルとアドレスの対応辞書。
            kind (str): メッセージ中の疑似命令名（"DB" / "DW"）。
            high (int): 許容する上限値（下限は 0）。
            unit (str): メッセージ中の単位名（"byte" / "word"）。

        Returns:
            tuple[int, int]: (評価結果, 消費したトークン数)。

        Raises:
            ValueError: 未解決のラベル・不正な式、または範囲外の場合。
        """
        loc = _format_location(p["line"], p.get("file"))
        value, consumed = self.asm.encoder.evaluate_expression(
            p["asm"],
            index,
            address_map,
            p["line"],
            current_address=_current_address(p),
            file_name=p.get("file"),
        )

        if value is None:
            raise ValueError(
                f"Undefined label or invalid expression in {kind} on {loc}: {p['asm'][index]}"
            )

        if not (0 <= value <= high):
            raise ValueError(
                f"{kind} value out of {unit} range (0-{high}) on {loc}: {value}"
            )

        return value, consumed

    def _split_operands(self, tokens):
        """トークンリストをカンマ区切りで分割してオペランドのリストを返します。

        Args:
            tokens (list): 分割対象のトークンリスト。

        Returns:
            list: カンマで分割されたオペランドのリスト（各要素はトークンのリスト）。
        """
        operands_list = []
        current_operand = []
        for token in tokens:
            if token == ",":
                if current_operand:
                    operands_list.append(current_operand)
                current_operand = []

            else:
                current_operand.append(token)

        if current_operand:
            operands_list.append(current_operand)

        return operands_list

    def _encode_dw_literal(self, value, token, line_num, file_name=None):
        """DW命令のリテラル値をエンコードします。

        Args:
            value (str or int): エンコードする値。
            token (str): エラー報告用の元トークン文字列。
            line_num (int): エラー報告用の行番号。
            file_name (str | None): エラー報告用のソース識別子。

        Returns:
            list: リトルエンディアンのバイトリスト（2バイト）。

        Raises:
            ValueError: 文字列が 1〜2 文字でない（空の `''` を含む）、数値が
                0〜65535 に収まらない、または文字列でも数値でもない場合。
        """
        loc = _format_location(line_num, file_name)
        if isinstance(value, str):
            # 空の '' も弾く（長さ 0 のまま進むと value[0] で IndexError になる）
            if not 1 <= len(value) <= 2:
                raise ValueError(
                    f"String literal in DW must be 1 or 2 characters on {loc}: {token}"
                )
            val = (
                ord(value) if len(value) == 1 else (ord(value[0]) << 8) | ord(value[1])
            )
            return [val & 0xFF, (val >> 8) & 0xFF]

        if isinstance(value, int):
            if not (0 <= value <= 65535):
                raise ValueError(
                    f"DW value out of word range (0-65535) on {loc}: {value}"
                )
            return [value & 0xFF, (value >> 8) & 0xFF]

        raise ValueError(f"Unsupported literal type for DW on {loc}: {token}")
