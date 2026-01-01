#!/usr/bin/env python3

import ast


class ExpressionEvaluator:
    """
    トークンリストから数式や論理式を解析・評価します。
    整数の算術、ラベル、演算子の優先順位をサポートします。
    """
    def __init__(self, tokens, label_map, line_num, cpu, defined_labels_pass1=None):
        """
        評価器を初期化します。

        Args:
            tokens (list): 式を表す文字列トークンのリスト。
            label_map (dict): ラベル名とそのアドレスを対応付けるマップ（パス2用）。
            line_num (int): エラー報告用の現在の行番号。
            cpu (Z80): 予約語にアクセスするためのZ80インスタンス。
            defined_labels_pass1 (set): 定義済みの全ラベルのセット（パス1の検証用）。
        """
        self.tokens = tokens
        self.label_map = label_map
        self.line_num = line_num
        self.cpu = cpu
        self.defined_labels_pass1 = defined_labels_pass1
        self.idx = 0

    def peek(self):
        """次のトークンを消費せずに返します。"""
        return self.tokens[self.idx] if self.idx < len(self.tokens) else None

    def consume(self):
        """次のトークンを消費します。"""
        self.idx += 1

    def _parse_char_literal(self, token):
        """文字リテラルを解析して数値を返します。"""
        try:
            v = ast.literal_eval(token)
        except (ValueError, SyntaxError) as e:
            raise ValueError(f"Invalid character literal '{token}' on line {self.line_num}") from e

        if isinstance(v, str):
            if len(v) == 1:
                return ord(v)
            elif len(v) == 2:
                return (ord(v[0]) << 8) | ord(v[1])
            raise ValueError("String literal in expression must be 1 or 2 characters")

        raise ValueError(f"Invalid literal type '{token}' on line {self.line_num}")

    def parse_factor(self):
        """因子（数値、ラベル、または括弧で囲まれた式）を解析します。"""
        token = self.peek()
        if token is None:
            raise ValueError(f"Unexpected end of expression on line {self.line_num}")

        if token == '(':
            self.consume()  # '('
            val = self.parse_add_sub()
            if self.peek() != ')':
                raise ValueError(f"Mismatched parentheses in expression on line {self.line_num}")
            self.consume()  # ')'
            return val
        
        if token == '-':
            self.consume()
            return -self.parse_factor()
        
        if token == '+':
            self.consume()
            return self.parse_factor()

        self.consume()
        # Character literals
        if (token.startswith("'") and token.endswith("'")) or \
           (token.startswith('"') and token.endswith('"')):
            return self._parse_char_literal(token)

        # Numeric literals
        try:
            return int(token, 0)
        except ValueError:
            # 数値ではないため、ラベルとみなす
            pass

        # Reserved word check
        if token.lower() in self.cpu.reserved:
             raise ValueError(f"Reserved word '{token}' cannot be used in expression on line {self.line_num}")

        # Pass 2: マップからラベルを解決
        if self.label_map is not None:
            if token in self.label_map:
                return self.label_map[token]
            else:
                raise ValueError(f"Undefined label or invalid term '{token}' in expression on line {self.line_num}")
        
        # Pass 1: ラベルが定義されているかチェックし、プレースホルダーとして0を返す
        else:
            if self.defined_labels_pass1 is not None:
                 if token not in self.defined_labels_pass1:
                     raise ValueError(f"Undefined symbol '{token}' in line {self.line_num}")
            return 0 # Placeholder value for pass 1

    def parse_mul_div(self):
        """乗算と除算を解析します。"""
        val = self.parse_factor()
        while self.peek() in ('*', '/'):
            op = self.peek()
            self.consume()
            rhs = self.parse_factor()
            if op == '*':
                val *= rhs
            else:
                if rhs == 0:
                    raise ValueError(f"Division by zero in expression on line {self.line_num}")
                val //= rhs
        return val

    def parse_add_sub(self):
        """加算と減算を解析します。"""
        val = self.parse_mul_div()
        while self.peek() in ('+', '-'):
            op = self.peek()
            self.consume()
            rhs = self.parse_mul_div()
            if op == '+':
                val += rhs
            else:
                val -= rhs
        return val

    def evaluate(self):
        """トークンリストから式全体を評価します。"""
        if not self.tokens:
            return None
        
        value = self.parse_add_sub()
        if self.idx != len(self.tokens):
            raise ValueError(f"Invalid expression syntax near '{self.peek()}' on line {self.line_num}")
        return value
