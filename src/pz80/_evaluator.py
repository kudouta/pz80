#!/usr/bin/env python3

import ast


class ExpressionEvaluator:
    """
    トークンリストから数式や論理式を解析・評価します。
    整数の算術、ラベル、演算子の優先順位をサポートします。

    優先順位 (低 → 高):
        |  →  ^  →  &  →  << >>  →  + -  →  * / %  →  単項(- + ~)  →  アトム
    """

    def __init__(
        self,
        tokens,
        reserved,
        line_num,
        address_map=None,
        defined_labels_pass1=None,
        current_address=None,
        file_name=None,
    ):
        """ExpressionEvaluatorを初期化します。

        Args:
            tokens (list): 評価するトークンのリスト。
                例: ["LABEL", "+", "1"]  または  ["0x10"]
            reserved (set): 予約語のセット。
                例: {"a", "b", "hl", "nop", ...}
            line_num (int): エラー報告に使用する行番号。
            address_map (dict, optional): ラベルとアドレスの対応辞書 (Pass 2用)
                例: {"LABEL": 0x0100, "SUB": 0x0200}
                Noneの場合はPass 1として扱い、未定義ラベルには仮値 0 を返す。
            defined_labels_pass1 (set, optional): Pass 1で定義済みラベルのセット
                例: {"LABEL", "SUB"}
            current_address (int, optional): $ の値として使用する現在のアドレス。
            file_name (str, optional): エラー報告で使用するソース識別子。
        """
        self.tokens = tokens
        self.reserved = reserved
        self.line_num = line_num
        self.address_map = address_map
        self.defined_labels_pass1 = defined_labels_pass1
        self.current_address = current_address
        self.file_name = file_name
        self.idx = 0

    def _loc(self):
        """エラーメッセージ用の位置情報文字列。

        Returns:
            str: "line 5 in main.asm" または "line 5" の形式。
        """
        return (
            f"line {self.line_num} in {self.file_name}"
            if self.file_name
            else f"line {self.line_num}"
        )

    def evaluate(self):
        """トークンリストから式全体を評価します。

        Returns:
            int or None: 式全体の評価結果。トークンが空の場合はNone。

        Raises:
            ValueError: 式の後ろに余分なトークンが残った場合。下位の解析が出す
                エラー（ゼロ除算、括弧の不一致、未定義シンボルなど）もそのまま届く。
        """
        if not self.tokens:
            return None

        value = self._parse_expr()
        if self.idx != len(self.tokens):
            raise ValueError(
                f"Invalid expression syntax near '{self._peek()}' on {self._loc()}"
            )
        return value

    def _peek(self):
        """次のトークンを消費せずに返します。

        Returns:
            str | None: 次のトークン。末尾に達していれば None。
        """
        return self.tokens[self.idx] if self.idx < len(self.tokens) else None

    def _advance(self):
        """次のトークンを返して消費します。

        呼ぶ前に `_peek()` で、トークンが残っていることを確かめておきます。

        Returns:
            str: 消費したトークン。
        """
        token = self.tokens[self.idx]
        self.idx += 1
        return token

    # ------------------------------------------------------------------
    # 二項演算子 (優先順位: 低 → 高)
    # ------------------------------------------------------------------

    def _parse_expr(self):
        """式全体の評価入口。最低優先度の演算子から解析を開始します。

        括弧の中の式もここから解析します（`_parse_primary()` から呼ばれる）。

        Returns:
            int: 式の値。
        """
        return self._parse_logical_or()

    def _parse_logical_or(self):
        """論理OR ( || ) を左結合で解析します。結果は 0 か 1。

        定数式なので短絡評価はしません（副作用が無く、右辺も必ず評価されます）。

        Returns:
            int: `1 || 0` なら 1。演算子が無ければ下位の値をそのまま返す。
        """
        val = self._parse_logical_and()
        while self._peek() == "||":
            self._advance()
            rhs = self._parse_logical_and()
            val = int(bool(val) or bool(rhs))
        return val

    def _parse_logical_and(self):
        """論理AND ( && ) を左結合で解析します。結果は 0 か 1。

        Returns:
            int: `1 && 0` なら 0。演算子が無ければ下位の値をそのまま返す。
        """
        val = self._parse_or()
        while self._peek() == "&&":
            self._advance()
            rhs = self._parse_or()
            val = int(bool(val) and bool(rhs))
        return val

    def _parse_or(self):
        """ビットOR ( | ) を左結合で解析します。

        Returns:
            int: `0x0F | 0x30` なら 0x3F。
        """
        val = self._parse_xor()
        while self._peek() == "|":
            self._advance()
            val = val | self._parse_xor()
        return val

    def _parse_xor(self):
        """ビットXOR ( ^ ) を左結合で解析します。

        Returns:
            int: `0xFF ^ 0x0F` なら 0xF0。
        """
        val = self._parse_and()
        while self._peek() == "^":
            self._advance()
            val = val ^ self._parse_and()
        return val

    def _parse_and(self):
        """ビットAND ( & ) を左結合で解析します。

        Returns:
            int: `0x1234 & 0xFF` なら 0x34。
        """
        val = self._parse_equality()
        while self._peek() == "&":
            self._advance()
            val = val & self._parse_equality()
        return val

    def _parse_equality(self):
        """等価比較 ( == != ) を左結合で解析します。結果は 0 か 1。

        Returns:
            int: `3 == 3` なら 1。演算子が無ければ下位の値をそのまま返す。
        """
        val = self._parse_relational()
        while self._peek() in ("==", "!="):
            op = self._advance()
            rhs = self._parse_relational()
            val = int(val == rhs) if op == "==" else int(val != rhs)
        return val

    def _parse_relational(self):
        """大小比較 ( < <= > >= ) を左結合で解析します。結果は 0 か 1。

        Returns:
            int: `2 < 3` なら 1。演算子が無ければ下位の値をそのまま返す。
        """
        comparisons = {
            "<": lambda a, b: a < b,
            "<=": lambda a, b: a <= b,
            ">": lambda a, b: a > b,
            ">=": lambda a, b: a >= b,
        }
        val = self._parse_shift()
        while self._peek() in comparisons:
            op = self._advance()
            rhs = self._parse_shift()
            val = int(comparisons[op](val, rhs))
        return val

    def _parse_shift(self):
        """シフト演算子 ( << >> ) を左結合で解析します。

        Returns:
            int: `1 << 4` なら 16。
        """
        val = self._parse_add()
        while self._peek() in ("<<", ">>"):
            op = self._advance()
            rhs = self._parse_add()
            val = val << rhs if op == "<<" else val >> rhs
        return val

    def _parse_add(self):
        """加減算 ( + - ) を左結合で解析します。

        Returns:
            int: `10 - 3 - 2` なら 5（左結合）。
        """
        val = self._parse_mul()
        while self._peek() in ("+", "-"):
            op = self._advance()
            rhs = self._parse_mul()
            val = val + rhs if op == "+" else val - rhs
        return val

    def _parse_mul(self):
        """乗除算・剰余 ( * / % ) を左結合で解析します。

        `/` は Python の `//` と同じく負の無限大へ丸めます（`-7 / 2` は -4）。

        Returns:
            int: `7 / 2` なら 3、`7 % 2` なら 1。

        Raises:
            ValueError: `/` の右辺が 0 の場合。
        """
        val = self._parse_unary()
        while self._peek() in ("*", "/", "%"):
            op = self._advance()
            rhs = self._parse_unary()
            if op == "*":
                val = val * rhs
            elif op == "/":
                if rhs == 0:
                    raise ValueError(f"Division by zero in expression on {self._loc()}")
                val = val // rhs
            else:
                val = val % rhs
        return val

    # ------------------------------------------------------------------
    # 単項演算子・アトム
    # ------------------------------------------------------------------

    def _parse_unary(self):
        """単項演算子 ( - + ~ ! ) を解析します。

        単項演算子は重ねられます（`--1` は 1）。

        Returns:
            int: `-1` なら -1、`~0` なら -1、`!5` なら 0。
        """
        token = self._peek()
        if token == "-":
            self._advance()
            return -self._parse_unary()
        if token == "+":
            self._advance()
            return self._parse_unary()
        if token == "~":
            self._advance()
            return ~self._parse_unary()
        if token == "!":
            # 論理否定。結果は 0 か 1（ビット否定の ~ とは別物）
            self._advance()
            return int(not self._parse_unary())
        return self._parse_primary()

    def _parse_primary(self):
        """アトム（数値・ラベル・$ ・括弧式）を解析します。

        Returns:
            int: アトムの値。数値は `int(token, 0)` で読み、文字リテラルは
                `_parse_char_literal()`、それ以外は `_resolve_symbol()` に任せる。

        Raises:
            ValueError: 式が途中で終わった、現在アドレスが分からないのに `$` を
                使った、または括弧が閉じていない場合。
        """
        token = self._peek()
        if token is None:
            raise ValueError(f"Unexpected end of expression on {self._loc()}")

        if token == "$":
            self._advance()
            if self.current_address is None:
                raise ValueError(
                    f"'$' used but current address is unknown on {self._loc()}"
                )
            return self.current_address

        if token == "(":
            self._advance()
            val = self._parse_expr()
            if self._peek() != ")":
                raise ValueError(
                    f"Mismatched parentheses in expression on {self._loc()}"
                )
            self._advance()
            return val

        token = self._advance()

        if token[0] in ('"', "'"):
            return self._parse_char_literal(token)

        try:
            return int(token, 0)
        except ValueError:
            pass

        return self._resolve_symbol(token)

    def _resolve_symbol(self, token):
        """シンボル（ラベル・EQU定数）を値に解決します。

        ここだけは式の文法ではなく、pz80 の 2 パス方式に固有の規約です。
        `address_map` の有無でどちらのパスかを判別します。

        * **Pass 2** (`address_map` あり): マップから実アドレスを引く。
          見つからなければエラー。
        * **Pass 1** (`address_map` が None): アドレスはまだ確定していないため
          プレースホルダーの 0 を返す。ただし `defined_labels_pass1` が
          与えられていれば定義済みかどうかだけは検証する。

        Args:
            token (str): 解決するシンボル名。

        Returns:
            int: 解決した値。Pass 1 では常に 0。

        Raises:
            ValueError: 予約語、または未定義シンボルの場合。
        """
        if token.lower() in self.reserved:
            raise ValueError(
                f"Reserved word '{token}' cannot be used in expression on {self._loc()}"
            )

        if self.address_map is not None:
            if token not in self.address_map:
                raise ValueError(
                    f"Undefined label or invalid term '{token}' in expression on {self._loc()}"
                )
            return self.address_map[token]

        if (
            self.defined_labels_pass1 is not None
            and token not in self.defined_labels_pass1
        ):
            raise ValueError(f"Undefined symbol '{token}' on {self._loc()}")
        return 0

    def _parse_char_literal(self, token):
        """文字リテラルを解析して数値を返します。

        Args:
            token (str): 解析対象の文字リテラルトークン（例: `'A'`, `"AB"`）。1〜2文字の文字列であること。

        Returns:
            int: 文字コードの数値。2文字の場合は上位バイトに1文字目、下位バイトに2文字目を格納した値。

        Raises:
            ValueError: リテラルとして読めない、または 1〜2 文字でない場合。
        """
        try:
            v = ast.literal_eval(token)
        except (ValueError, SyntaxError) as e:
            raise ValueError(
                f"Invalid character literal '{token}' on {self._loc()}"
            ) from e

        # 空の '' も弾く。長さ 0 のまま進むと v[0] で IndexError になり、
        # 行番号の無いエラーで止まっていた
        if not isinstance(v, str) or not 1 <= len(v) <= 2:
            raise ValueError(
                f"String literal in expression must be 1 or 2 characters on {self._loc()}"
            )

        return ord(v) if len(v) == 1 else (ord(v[0]) << 8) | ord(v[1])
