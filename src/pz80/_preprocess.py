#!/usr/bin/env python3

"""アセンブル前処理（シンボル定義・条件アセンブル・行の構造化）。

`asm.py` の 4 責務のうち「前処理」を担う。トークン化済みの行を受け取り、
`ORG` / `EQU` / `IF` / `ELSEIF` / `ELSE` / `ENDIF` とラベル定義を解釈して、
Pass 1 が扱える構造化リストと labelmap を返す。

`_pass0.py` ではなく `_preprocess.py` としているのは、`_pass0` / `_op0` のような
**位置や番号による命名**が分割前の読みにくさの一因だったため。ここで扱う疑似命令は
「**どの行が存在するか**」と「**シンボルが何を意味するか**」を決めるもので、
バイト生成の前段に位置する。C プリプロセッサと同じ役割にあたる。

> **`_directives.py` との境界**: DB / DW / DS は**バイトを出力する**ので命令と同類、
> つまり符号化側に属する。ORG / EQU / IF は出力せず解釈を変えるだけなので前処理側。

labelmap はこの層の出力なので、`Asm` から受け取るのではなく `run()` が返す。
"""

import re

from pz80 import _evaluator
from pz80._symbols import SymbolTable
from pz80._util import format_location as _format_location

# ラベル先頭文字チェック用
_RE_LABEL_START = re.compile(r"^[A-Za-z@]+")


class Preprocessor:
    """トークン列を構造化し、シンボル表を構築する。

    Args:
        cpu (z80.Z80): 予約語判定に使う CPU 定義。

    Attributes:
        labelmap (list): 抽出したラベル・EQU 定数のリスト。`run()` の副産物。
    """

    def __init__(self, cpu):
        """Preprocessor を初期化します。"""
        self.cpu = cpu
        self.symbols = SymbolTable()
        self.consumed = []

    @property
    def labelmap(self):
        """シンボル表を `labelmap` 形式で返します（`SymbolTable` へのビュー）。

        Returns:
            list[dict]: `[{'type', 'symbol', 'value'}, ...]`。
        """
        return self.symbols.as_labelmap()

    def run(self, src):
        """前処理を実行します。

        `pass0()` で構造化したうえで、`equ()` により EQU シンボルを数値へ置換します。

        Args:
            src (list): _tokenizer.source() が生成したアセンブルリスト。

        Returns:
            list: 構造化されたアセンブルリスト。labelmap は `self.labelmap` に、
                出力行を持たない行の記録は `self.consumed` に残る。
        """
        self.symbols = SymbolTable()
        self.consumed = []
        asm = self.pass0(src)
        self.equ(asm)
        return asm

    def equ(self, asm):
        """EQUラベルを全て数値へ置換

        Args:
            asm (list): アセンブルリスト。
                        各要素は {"line": int, "asm": [str, ...]} の形式。
                        EQUシンボルと一致するトークンを対応する数値文字列に直接置換する。

        Note:
            `labelmap` の値は int なので、トークン列へ入れる際に `str()` を掛ける。
            トークンは文字列でなければならず、後段の評価器が 10 進として読み直す。
        """
        # EQU総当たり置換。シンボルは大文字正規化済みのため、トークン側も大文字で比較する。
        for symbol, value in self.symbols.equ_items():
            for q in asm:
                if "asm" in q:
                    for r, tok in enumerate(q["asm"]):
                        if symbol == tok.upper():
                            q["asm"][r] = str(value)

    def pass0(self, src):
        """アセンブル事前準備

        ラベル・EQU・ORGを検出して labelmap を構築し、構造化されたアセンブルリストを返す。

        Args:
            src (list): _tokenizer.source() が生成したアセンブルリスト。
                        例: [{"line": 1, "asm": ["LABEL", ":", "ld", "a", ",", "0x10"]}, ...]

        Returns:
            list: 構造化されたアセンブルリスト。
                  各要素は命令行かラベル行のいずれかのリスト形式。
                    (1) 命令行:
                          "line": int
                          "asm": [str, ...]
                          "base": int
                          "offset": int
                        例: ["line": 1, "asm": ["ld", "a", ",", "0x10"], "base": 0, "offset": 0]

                    (2) ラベル行:
                          "line": int
                          "label": str
                          "base": int
                          "offset": int
                        例: ["line": 1, "label": "LABEL", "base": 0, "offset": 0]

                    ※ラベル + 命令行は2つのリストに分離される。ORG/EQU行は結果リストに含まれない。

        Raises:
            ValueError: ラベルが予約語・重複・不正フォーマット、またはORG/EQU値が不正な場合。
        """
        result = []
        self.consumed = []
        # ORG 未指定のソースは 0 番地開始として扱う。
        # 以前は -1（アドレス未確定）で初期化していたが、その -1 が
        # そのままアドレスとして使われ、`jp label` が JP 0xFFFF になっていた。
        base = 0
        defined_symbols = set()
        cond_stack = []

        for entry in src:
            asm = entry["asm"]
            loc = _format_location(entry["line"], entry.get("file"))
            kind = self._classify_line(asm)

            # 条件アセンブルの制御行は、無効ブロック内でも解釈する必要がある
            # （ネストした IF/ENDIF の対応を取るため）。
            if kind in ("if", "elseif", "else", "endif"):
                self._handle_conditional(
                    kind, asm, cond_stack, entry["line"], entry.get("file"), loc
                )
                self._consume(result, entry, kind)
                continue

            # 無効ブロック内の行は結果に含めない
            if cond_stack and not cond_stack[-1]["active"]:
                self._consume(result, entry, "skipped")
                continue

            # ORG はラベルを伴わないので、ラベル検証には進ませない。
            if kind == "org":
                base = self._parse_org(asm, loc)
                self._consume(result, entry, "org")
                continue

            if kind == "instruction":
                result.append(self._row(entry, base, asm=asm))
                continue

            # 残るのはラベル定義を伴う行（equ / label / label+opcode）
            rows = self._define_symbol(kind, entry, base, defined_symbols, loc)
            if not rows:  # EQU は labelmap に登録するだけで出力行を持たない
                self._consume(result, entry, "equ")
            result += rows

        if cond_stack:
            raise ValueError(f"Unterminated IF (started on {cond_stack[-1]['loc']})")

        return result

    def _consume(self, result, entry, kind):
        """出力行を持たない行を `self.consumed` に記録します。

        `EQU` / `ORG` / `IF` / `ELSEIF` / `ELSE` / `ENDIF` と、条件が偽で捨てられた行は
        構造化リストに現れません。そのため利用者から見ると「行が消えた」理由が
        **疑似命令として消費されたのか、条件アセンブルで落とされたのか**を
        区別できませんでした。`kind` を添えて記録することで区別できます。

        **`base` / `offset` は持たせません。** これらの行は番地を占有せず、
        Pass 1 を通らないため offset が確定しないためです。持たせると
        `base + offset` が実際の位置とずれた値になり、かえって誤解を招きます。

        `result` への挿入位置を持たせるのは、`assemble_chunks()` が Pass 2 の後で
        元の並び順どおりに戻せるようにするためです。Pass 1/2 に渡るリストへは
        入れません（符号化対象と混ざらないようにするため）。

        Args:
            result (list): 構築中の構造化リスト。現在の長さを挿入位置に使う。
            entry (dict): 元のアセンブル行。
            kind (str): "equ" / "org" / "if" / "elseif" / "else" / "endif" /
                "skipped"。
        """
        self.consumed.append(
            (
                len(result),
                {
                    "line": entry["line"],
                    "file": entry.get("file"),
                    "asm": entry["asm"],
                    "kind": kind,
                },
            )
        )

    @staticmethod
    def _row(entry, base, **fields):
        """構造化リストの 1 行を作ります。

        Args:
            entry (dict): 元のアセンブル行（位置情報の取得に使う）。
            base (int): ベースアドレス。
            **fields: `asm` または `label` のいずれか。

        Returns:
            dict: 構造化された 1 行。
        """
        return {
            "line": entry["line"],
            "file": entry.get("file"),
            **fields,
            "base": base,
            "offset": 0,
        }

    def _define_symbol(self, kind, entry, base, defined_symbols, loc):
        """ラベル定義を伴う行を処理し、出力すべき行を返します。

        EQU は labelmap に登録するだけで出力行を持ちません。ラベルは 1 行、
        ラベル + 命令は 2 行（ラベル行と命令行に分離）を返します。

        Args:
            kind (str): "equ" / "label" / "label+opcode"。
            entry (dict): 元のアセンブル行。
            base (int): ベースアドレス。
            defined_symbols (set): 定義済みシンボル集合（この場で追加される）。
            loc (str): エラーメッセージ用の位置情報。

        Returns:
            list: 構造化リストへ追加する行のリスト。
        """
        asm = entry["asm"]

        # EQU シンボルは大文字に正規化して大文字小文字を区別しない。
        # ニーモニックが case-insensitive なのと同様、EQU定数も統一する。
        symbol = asm[0].upper() if kind == "equ" else asm[0]
        self._validate_label(asm[0], symbol, defined_symbols, loc)
        defined_symbols.add(symbol)

        if kind == "equ":
            value = self._eval_equ(asm, entry["line"], entry.get("file"), loc)
            self.symbols.define_equ(symbol, value)
            return []

        # ラベルのアドレスは Pass 1 まで確定しないので 0 を置く。
        # Pass 2 が `Asm._pass2` で確定アドレスに書き換える。
        self.symbols.define_label(asm[0])
        rows = [self._row(entry, base, label=asm[0])]

        if kind == "label+opcode":
            # ラベルとオペコードを2行へ分離
            rows.append(self._row(entry, base, asm=asm[2:]))
        return rows

    def _handle_conditional(self, kind, asm, cond_stack, line, file, loc):
        """条件アセンブル (IF / ELSEIF / ELSE / ENDIF) の状態を更新します。

        フレームは "taken"（この IF 連鎖でいずれかの枝が採用済みか）を保持します。
        ELSEIF はこの "taken" を見て、既に採用済みなら自分の条件式を見もせずに
        無効になります。ELSE と同じ規則で動くので、状態は増えません。

        条件式を評価しないのは 2 つの場合です。**親ブロックが無効なとき**と、
        **同じ連鎖で既にどれかの枝が採用済みのとき**。どちらも到達しない枝で、
        その中の未定義シンボルをエラーにしないためです。

        Args:
            kind (str): "if" / "elseif" / "else" / "endif"。
            asm (list): トークンリスト。
            cond_stack (list): 条件フレームのスタック（この場で更新される）。
            line (int): 行番号。
            file (str | None): ソース識別子。
            loc (str): エラーメッセージ用の位置情報。

        Raises:
            ValueError: 対応する IF が無い、ELSE が重複、ELSE より後に ELSEIF が
                現れた、または条件式が不正な場合。
        """
        if kind == "if":
            parent_active = (not cond_stack) or cond_stack[-1]["active"]
            if parent_active:
                taken = bool(self._eval_condition(asm[1:], line, file, loc, "IF"))
            else:
                # 親が無効なら中身は一切採用しない（ELSE も含めて）
                taken = True
            cond_stack.append(
                {
                    "active": parent_active and taken,
                    "taken": taken,
                    "else_seen": False,
                    "parent_active": parent_active,
                    "loc": loc,
                }
            )
            return

        if not cond_stack:
            raise ValueError(f"{kind.upper()} without matching IF on {loc}")

        if kind == "endif":
            cond_stack.pop()
            return

        frame = cond_stack[-1]
        if frame["else_seen"]:
            if kind == "else":
                raise ValueError(f"Duplicate ELSE on {loc}")
            raise ValueError(f"ELSEIF after ELSE on {loc}")

        if kind == "elseif":
            if frame["taken"]:
                # 採用済みの枝がある。条件式は評価しない。
                # 親が無効な IF は taken=True で積まれるので、この枝も通る。
                frame["active"] = False
            else:
                cond = self._eval_condition(asm[1:], line, file, loc, "ELSEIF")
                frame["active"] = frame["parent_active"] and bool(cond)
                frame["taken"] = frame["active"]
            return

        frame["else_seen"] = True
        frame["active"] = frame["parent_active"] and not frame["taken"]
        frame["taken"] = frame["taken"] or frame["active"]

    def _eval_condition(self, tokens, line, file, loc, keyword):
        """IF / ELSEIF の条件式を評価します。

        条件は `_pass0` の時点で確定していなければなりません。IF は行の有無を
        変え、それ以降の全アドレスに影響するためです。したがって参照できるのは
        既に定義済みの EQU 定数だけで、ラベル（アドレスが pass1 まで未確定）は
        使えません。未定義シンボルはエラーになります。

        Args:
            tokens (list): 条件式のトークンリスト。
            line (int): 行番号。
            file (str | None): ソース識別子。
            loc (str): エラーメッセージ用の位置情報。
            keyword (str): エラーメッセージに出す keyword（"IF" / "ELSEIF"）。
                どちらの行で失敗したかが分かるようにするため。

        Returns:
            int: 評価結果。0 以外が真。

        Raises:
            ValueError: 条件式が空、未定義シンボルを含む、または評価に失敗した場合。
        """
        if not tokens:
            raise ValueError(f"{keyword} requires a condition on {loc}")

        resolved = self._resolve_equ_symbols(tokens)

        try:
            # address_map={} を渡すと、未解決のシンボルはエラーになる
            ev = _evaluator.ExpressionEvaluator(
                resolved, self.cpu.reserved, line, address_map={}, file_name=file
            )
            value = ev.evaluate()
        except ValueError as e:
            raise ValueError(f"Invalid {keyword} condition on {loc}: {e}") from e

        if value is None:
            raise ValueError(
                f"Invalid {keyword} condition on {loc}: {' '.join(tokens)}"
            )
        return value

    def _classify_line(self, asm):
        """トークン列から行種別を判定します。

        判定のみを行い、検証や登録は行いません。ORG 行がラベル用の検証を
        通過してしまう問題を避けるため、種別ごとの処理は呼び出し側で分けます。

        Args:
            asm (list): トークンリスト (例: ["LABEL", ":", "ld", "a", ",", "0x10"])

        Returns:
            str: "org" / "equ" / "if" / "elseif" / "else" / "endif" / "label" /
                "label+opcode" / "instruction" のいずれか。
        """
        len_org = 2
        len_equ = 4
        len_label = 2

        has_colon = len(asm) >= len_label and asm[1] == ":"

        # `org:` のようにラベル定義の形をしているものは疑似命令扱いにしない。
        # ラベルとして扱い、予約語チェックで弾かれるようにする。
        if (len(asm) == len_org) and (asm[0].lower() == "org") and not has_colon:
            return "org"

        if not has_colon and asm[0].lower() in ("if", "elseif", "else", "endif"):
            return asm[0].lower()

        if not has_colon:
            return "instruction"

        if (len(asm) >= len_equ) and (asm[2].lower() == "equ"):
            return "equ"

        return "label" if len(asm) == len_label else "label+opcode"

    def _parse_org(self, asm, loc):
        """ORG 行のアドレスを解決します。

        Args:
            asm (list): トークンリスト (例: ["org", "0x0100"])
            loc (str): エラーメッセージ用の位置情報。

        Returns:
            int: 解決したアドレス。

        Raises:
            ValueError: アドレス書式が不正な場合。
        """
        try:
            return int(asm[1], 0)
        except ValueError as e:
            raise ValueError(
                f"Invalid address format for ORG on {loc}: {asm[1]}"
            ) from e

    def _validate_label(self, name, symbol, defined_symbols, loc):
        """ラベル名を検証します。

        Args:
            name (str): ソース上のラベル名。
            symbol (str): 重複判定に使う正規化後のシンボル名。
            defined_symbols (set): 定義済みシンボルの集合。
            loc (str): エラーメッセージ用の位置情報。

        Raises:
            ValueError: 予約語・重複・書式不正の場合。
        """
        if name.lower() in self.cpu.reserved:
            raise ValueError(f"Invalid label '{name}' on {loc}: Reserved word")

        if symbol in defined_symbols:
            raise ValueError(f"Duplicate label definition '{name}' on {loc}")

        if _RE_LABEL_START.search(name) is None:
            raise ValueError(f"Invalid label format '{name}' on {loc}")

    def _eval_equ(self, asm, line, file, loc):
        """EQU 行の右辺を評価します。

        **既に定義済みの EQU 定数を参照できます**（後方参照）。

            WIDTH:  equ 8
            HEIGHT: equ 8
            AREA:   equ WIDTH * HEIGHT   ; 64

        前方参照とラベル参照は未対応です。EQU の値は `ds` / `db` のサイズに影響し、
        それがアドレスに影響するため、前方参照を許すと「値を決めるためにアドレスが
        要り、アドレスを決めるために値が要る」という循環になり得ます。ラベルの
        アドレスも Pass 1 まで確定せず、EQU の置換はそれより前に行われます。

        Args:
            asm (list): トークンリスト (例: ["VAL", ":", "equ", "0x10"])
            line (int): 行番号。
            file (str | None): ソース識別子。
            loc (str): エラーメッセージ用の位置情報。

        Returns:
            int: 評価結果。

        Raises:
            ValueError: 式が不正、未定義シンボルを含む、
                または値が 0-65535 の範囲外の場合。
        """
        maxword = 0xFFFF
        resolved = self._resolve_equ_symbols(asm[3:])
        try:
            # address_map={} を渡すと、未解決のシンボルはエラーになる
            ev = _evaluator.ExpressionEvaluator(
                resolved, self.cpu.reserved, line, address_map={}, file_name=file
            )
            value = ev.evaluate()
        except ValueError as e:
            raise ValueError(f"Invalid expression for EQU on {loc}: {asm[0]}") from e

        if value is None or (value > maxword) or (value < 0):
            raise ValueError(
                f"EQU value out of range (0-{maxword}) on {loc}: {asm[0]} = {value}"
            )
        return value

    def _resolve_equ_symbols(self, tokens):
        """トークン列の中の定義済み EQU 定数を値に置換します。

        ニーモニック同様、シンボルの大文字小文字は区別しません。
        `_pass0` は EQU を出現順に `labelmap` へ積むため、ここで参照できるのは
        その行より前に定義された定数だけになります（後方参照）。

        Args:
            tokens (list): 置換対象のトークンリスト。

        Returns:
            list: 置換後のトークンリスト。

        Note:
            `labelmap` の値は int なので、トークン列へ入れる際に `str()` を掛ける。
        """
        equ_values = {k: str(v) for k, v in self.symbols.equ_items()}
        return [equ_values.get(t.upper(), t) for t in tokens]
