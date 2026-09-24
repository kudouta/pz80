#!/usr/bin/env python3

"""シンボル表。

`EQU` 定数とラベルを 1 つの表で保持する。両者の違いは **値が確定する時期**だけで、
`EQU` は定義時に、ラベルは Pass 1 でアドレスが決まる。

以前は同じ情報が 4 つの形で散らばっていた。

* `labelmap`        `[{'type', 'symbol', 'value'}]`  前処理が構築
* `label2address`   `[{'label', 'address'}]`         Pass 1 が構築
* `defined_labels`  `set`                            Pass 1 の間だけ存在
* `address_map`     `dict`                           Pass 2 が毎回作り直す

`labelmap` と `label2address` は公開 API なので、この表からのビューとして残す。

> **キーの正規化規則が種別で異なる。** `EQU` は大小文字を区別せず大文字へ正規化し
> （ニーモニックに合わせた）、ラベルは区別してソースに書かれたまま保持する。
> `Start` と `START` は別のラベルになる。この非対称は言語仕様なのでここで揃えない。
"""


class SymbolTable:
    """`EQU` 定数とラベルの表。

    定義順を保つ（`labelmap` の並びがソース上の定義順である前提を壊さないため）。
    """

    def __init__(self):
        """空の表を作ります。"""
        self._entries = []  # 定義順のエントリ
        self._by_key = {}  # 参照キー -> エントリ

    @staticmethod
    def key_of(name, kind):
        """参照に使うキーを返します。

        Args:
            name (str): ソースに書かれたシンボル名。
            kind (str): "equ" または "label"。

        Returns:
            str: `equ` なら大文字化した名前、`label` ならそのまま。
        """
        return name.upper() if kind == "equ" else name

    def define_equ(self, name, value):
        """`EQU` 定数を登録します。

        Args:
            name (str): シンボル名（大文字へ正規化して保持する）。
            value (int): 評価済みの値。
        """
        self._add("equ", self.key_of(name, "equ"), value)

    def define_label(self, name):
        """ラベルを登録します。アドレスは未確定（0）で入ります。

        Args:
            name (str): ラベル名（ソースに書かれたまま保持する）。
        """
        self._add("label", name, 0)

    def _add(self, kind, key, value):
        """エントリを追加します。

        Args:
            kind (str): "equ" または "label"。
            key (str): 参照キー。
            value (int): 値。
        """
        entry = {"type": kind, "symbol": key, "value": value}
        self._entries.append(entry)
        self._by_key[key] = entry

    def set_address(self, name, address):
        """ラベルの確定アドレスを設定します（Pass 1 が呼ぶ）。

        Args:
            name (str): ラベル名。
            address (int): 確定したアドレス。
        """
        self._by_key[name]["value"] = address

    def __contains__(self, name):
        """シンボルが定義済みか返します。

        Pass 1 の未定義シンボル判定に使います（以前の `defined_labels` の役割）。

        Args:
            name (str): 判定するシンボル名。

        Returns:
            bool: 定義済みなら True。
        """
        return name in self._by_key

    def is_label(self, name):
        """名前がラベルとして定義済みか返します。

        `DS` / `ALIGN` はサイズがそれ以降のアドレスを動かすため、Pass 1 で値が
        確定していなければならず、ラベルを参照できません。その判定に使います。
        `EQU` は前処理でトークンごと値へ置換済みなので、Pass 1 に残っている
        識別子のうちラベルだけを見分ければ足ります。

        Args:
            name (str): 判定する名前。

        Returns:
            bool: ラベルとして定義済みなら True。`EQU` や未定義なら False。
        """
        entry = self._by_key.get(name)
        return entry is not None and entry["type"] == "label"

    def equ_items(self):
        """`EQU` 定数を `(キー, 値)` の並びで返します。

        Returns:
            list[tuple[str, int]]: 定義順の `(大文字キー, 値)`。
        """
        return [(e["symbol"], e["value"]) for e in self._entries if e["type"] == "equ"]

    def addresses(self):
        """ラベル名から確定アドレスを引く辞書を返します。

        Pass 2 の式評価に渡します（以前の `address_map`）。`EQU` は
        前処理の段階でトークンごと値へ置換済みなので含めません。

        Returns:
            dict: `{ラベル名: アドレス}`。
        """
        return {e["symbol"]: e["value"] for e in self._entries if e["type"] == "label"}

    def as_labelmap(self):
        """公開属性 `Asm.labelmap` 用のビューを返します。

        Returns:
            list[dict]: `[{'type', 'symbol', 'value'}, ...]`（定義順）。
        """
        return self._entries

    def as_label2address(self):
        """公開属性 `Asm.label2address` 用のビューを返します。

        Returns:
            list[dict]: `[{'label', 'address'}, ...]`（定義順、ラベルのみ）。
        """
        return [
            {"label": e["symbol"], "address": e["value"]}
            for e in self._entries
            if e["type"] == "label"
        ]
