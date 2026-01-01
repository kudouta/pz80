#!/usr/bin/env python3

import ast


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
        """DB/DEFB 疑似命令のオペランドを解析します (Pass 1)。"""
        opcodes = []
        operands = item["asm"][1:]

        for operand in operands:
            if operand == ',':
                continue

            # 文字列リテラルの処理
            if operand.startswith('"') or operand.startswith("'"):
                try:
                    decoded_string = ast.literal_eval(operand)
                    opcodes.extend(ord(c) for c in decoded_string)
                except (ValueError, SyntaxError) as e:
                    raise ValueError(f"Invalid string literal in line {item['line']}: {operand}") from e
            else:
                # 数値の処理
                try:
                    value = int(operand, 0)
                except ValueError as e:
                    raise ValueError(f"Invalid operand for DB in line {item['line']}: {operand}") from e

                if not (0 <= value <= 255):
                    raise ValueError(f"DB value out of byte range (0-255) in line {item['line']}: {value}")
                opcodes.append(value)
        return opcodes

    def _split_operands(self, tokens):
        """トークンリストをカンマ区切りで分割してオペランドのリストを返します。"""
        operands_list = []
        current_operand = []
        for token in tokens:
            if token == ',':
                if current_operand:
                    operands_list.append(current_operand)
                current_operand = []
            else:
                current_operand.append(token)
        if current_operand:
            operands_list.append(current_operand)
        return operands_list

    def _encode_dw_literal(self, value, token, line_num):
        """DW命令のリテラル値をエンコードします。"""
        if isinstance(value, str):
            if len(value) == 1:
                val = ord(value)
            elif len(value) == 2:
                val = (ord(value[0]) << 8) | ord(value[1])
            else:
                raise ValueError(f"String literal in DW must be 1 or 2 characters in line {line_num}: {token}")
            return [val & 0xFF, (val >> 8) & 0xFF]

        elif isinstance(value, int):
            if not (0 <= value <= 65535):
                raise ValueError(f"DW value out of word range (0-65535) in line {line_num}: {value}")
            return [value & 0xFF, (value >> 8) & 0xFF]

        else:
            raise ValueError(f"Unsupported literal type for DW in line {line_num}: {token}")

    def process_dw_pass1(self, item):
        """DW/DEFW 疑似命令のオペランドを解析します (Pass 1)。"""
        opcodes = []
        operands_list = self._split_operands(item["asm"][1:])

        for operand_tokens in operands_list:
            # オペランドが単一トークンかどうかで処理を分岐
            if len(operand_tokens) == 1:
                token = operand_tokens[0]
                try:
                    value = ast.literal_eval(token)  # Check if it's a valid literal
                except (ValueError, SyntaxError):
                    # 有効なPythonリテラルではないため、ラベルまたは式とみなす
                    pass
                else:
                    # It is a literal, so encode it. This can raise ValueError for invalid values.
                    opcodes.extend(self._encode_dw_literal(value, token, item['line']))
                    continue

            # ラベルまたは式として扱う (プレースホルダーを挿入)
            opcodes.extend([0x00, 0x00])
            
        return opcodes

    def process_dw_pass2(self, p, label_map):
        """DW/DEFW 疑似命令のアドレス解決を行います (Pass 2)。"""
        current_byte_offset = 0
        i = 1 # index into p["asm"], skip mnemonic
        while i < len(p["asm"]):
            token = p["asm"][i]
            
            if token == ',':
                i += 1
                continue

            address, consumed = self.asm._evaluate_expression(p["asm"], i, label_map, p["line"])

            if address is None:
                raise ValueError(f"Undefined label or invalid expression in DW at line {p['line']}: {p['asm'][i]}")

            if not (0 <= address <= 65535):
                raise ValueError(f"DW value out of word range (0-65535) in line {p['line']}: {address}")

            p["opcode"][current_byte_offset] = address & 0xFF
            p["opcode"][current_byte_offset + 1] = (address >> 8) & 0xFF
            
            current_byte_offset += 2
            i += consumed