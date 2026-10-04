#!/usr/bin/env python3

import re

from pz80 import z80
from pz80._vectors import parse_entry

# M1サイクル対象プレフィックスバイト
_Z80_PREFIXES = frozenset([0xDD, 0xFD, 0xED, 0xCB])

# `label_names` に使える名前。`\w` だけでは足りない —— `.` は pz80 のラベルとして
# 合法で（構造体レイアウトの `TASK.pos` が依存している）、`StrA.D.1980` のような
# 名前を実際に書きたくなる。逆にトークナイザの区切り文字（`,` `(` `)` など）を
# 含む名前は 1 トークンに収まらず、出力が再アセンブルできなくなる。
_LABEL_NAME_CHARS = r"[\w.]+"
_RE_LABEL_NAME = re.compile(rf"^{_LABEL_NAME_CHARS}$")

# `equ` の値に使える辞書キー。read と write で役割が違うハードウェアレジスタ
# （`0xE000` が読むと KeyIn、書くと IntEnable、など）を 1 エントリで書くため。
# この順が `EQU` 定義行の出力順にもなる。
_EQU_MODES = ("r", "w", "imm")

# 名前を探す順。`imm` は `LD hl, nn` のようにアドレスを読むとも書くとも決まらない
# 命令のためのキーで、**出力ラッチをまとめて初期化するループの先頭**がこの形に
# なりやすい。その場合は書き名が正しいので、読み名を先に見る既定では外れる。
#
# 向きが決まっている `r` / `w` では `imm` を先に見ない。`imm` は「向きが分から
# ないとき」の指定であって、分かっている命令の名前を上書きする意味はないため。
# ただし最後の候補には置く。`imm` だけを書いた設定で 3 命令すべてに効かせたい。
_EQU_FALLBACK = {
    "r": ("r", "w", "imm"),
    "w": ("w", "r", "imm"),
    "imm": ("imm", "r", "w"),
}

# `labels` の値に辞書を書くときのキー。`imm` を False にすると、そのアドレスは
# 16 ビット即値（`LD rr, nn`）の置き換えに使われなくなる。`comment` はラベル行の
# 前に出す説明。
_LABEL_KEYS = ("name", "imm", "comment")

# `equ` の値に辞書を書くときのキー。向きの名前（`_EQU_MODES`）に加えて、
# `name` は書いていない向きすべての名前、`comment` は `EQU` 行に付ける説明。
_EQU_KEYS = (*_EQU_MODES, "name", "comment")

# `comments` の値に辞書を書くときのキー。`line` は行末、`block` は行の前。
_COMMENT_KEYS = ("line", "block")


def _check_comment_text(what, key, text):
    """`labels` / `equ` の `comment` を検査します。空と空白だけは弾きます。

    `comments` の値と同じ扱いにしてあります。同じ種類の書き間違いなのに、
    書いた場所によって通ったり止まったりすると、どちらの規則だったかを毎回
    思い出すことになります。

    Args:
        what (str): 文面に出す種類（"label" / "equ"）。
        key: 設定に書かれたキー（文面に出す）。
        text: `comment` の値。

    Raises:
        ValueError: 文字列でない、または空・空白だけの場合。
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError(
            f"Invalid {what} comment for address {key}: {text!r} "
            f"(use a non-empty string)"
        )


# `data` の要素に辞書を書くときのキー。`fmt` は表の並び、`per_line` は 1 行に
# まとめるバイト数。
_DATA_KEYS = ("range", "fmt", "per_line")

# `fmt` に書ける型と、それが消費するバイト数。この 2 つだけ。`dd` のような
# 4 バイト型は Z80 のデータ表に出てこない。
_DATA_FMT_SIZES = {"b": 1, "w": 2}

# データ行の疑似命令。エラーの文面を「命令の途中」と書き分けるために使う。
_DATA_FMT_DIRECTIVES = ("db", "dw")


def _block_rows(text):
    """説明を `; …` の行の並びにします（改行ごとに 1 行）。無ければ空。

    Args:
        text (str | None): 説明。

    Returns:
        list[dict]: `{"asm": "; …"}` の並び。
    """
    return [{"asm": f"; {line}".rstrip()} for line in text.split("\n")] if text else []


class Disasm:
    """Z80逆アセンブラクラス"""

    def __init__(self):
        """Disasmクラスを初期化します。"""
        self.cpu = z80.Z80()
        self._datamap = []  # 逆アセンブル時にデーターとして扱うアドレス範囲テーブル
        self._data_fmt = []  # 書式を指定した範囲 [(開始, 終了, 型の並び, 1行のバイト数)]
        self.warnings = []  # 設定と実データの食い違い。CLI が stderr へ出す
        self._valid_ranges = None  # バイナリが実在する範囲。None なら全域
        # ラベル参照を拾う。`@名前` は省略可なので、接尾辞の無い従来の出力も通る。
        #
        # **区切りが `@` なのは偶然ではない。** `walk._RE_LABEL` が
        # `\bL_([0-9A-Fa-f]{4})\b` と末尾に \b を要求するため、4 桁の直後は
        # 非単語文字でなければならない。`_` も英数字も単語文字なので使えず、
        # 候補は `@` と `.` だけになる。`.` は構造体レイアウトのメンバ名
        # （`TASK.pos`）と見分けが付かないので `@` を選んだ。
        self._re_label = re.compile(rf"(L_([0-9a-fA-F]{{4}})(?:@{_LABEL_NAME_CHARS})?)")
        self._dispatch = {
            1: self._handle_1byte,
            2: self._handle_2bytes,
            3: self._handle_3bytes,
            4: self._handle_4bytes,
        }
        self.m1_handler = None  # M1サイクルハンドラー (address, byte) -> byte
        self.label_addresses = []  # 強制的にラベルを付与するアドレスのリスト
        self._label_names = {}
        self._label_no_imm = set()  # 16 ビット即値の置き換えに使わないアドレス
        self._raw_operand = set()  # オペランドを数値のまま出す命令のアドレス
        self._comments = {}  # 出力へ出すコメント {アドレス: {"line": …, "block": …}}
        self._label_comments = {}  # ラベル行の前に出す説明 {アドレス: 文字列}
        self._equ_names = {}
        self._equ_comments = {}  # EQU 行に付ける説明 {アドレス: 文字列}

    @property
    def equ_names(self):
        """`EQU` として名前を付けるアドレス `{アドレス: {"r": 名前, "w": 名前}}`。

        Returns:
            dict: キーは int、値は `r` / `w` / `imm` を持つ dict
                （一部だけのこともある）。
        """
        return self._equ_names

    @equ_names.setter
    def equ_names(self, value):
        """`EQU` 名を検証して設定します。

        **`labels` と役割が違います。** `labels` は「その行にラベルを貼る」機構で、
        貼る行が無いアドレス（RAM・I/O）には使えません。`equ` は逆アセンブル範囲の
        外を指す定数のためのもので、`MirrorRam: EQU 0x8000` を先頭に出し、
        参照側は**裸の名前**にします。住所を名前に残す必要はありません。
        住所は `EQU` の定義行にあり、それは手書きでも同じ場所だからです。

        値は文字列か、`name` / `r` / `w` / `imm` / `comment` を持つ dict です。

            equ = {
                0x8000: "MirrorRam",                      # 読み書き共通
                0xE000: {"r": "KeyIn", "w": "IntEnable"}, # 役割が違う
                0xE001: {"w": "SndVolume"},               # 書き専用
            }

        `imm` は `LD hl, nn` のように**アドレスを読むとも書くとも決まらない**
        命令のための名前です。既定では読み名を先に見ますが、この形はアーケード
        基板では**出力ラッチをまとめて初期化するループの先頭**になりやすく、
        そこでは書き名が正しい名前になります。

            0xE000: {"r": "KeyIn", "w": "IntEnable", "imm": "IntEnable"}

        `dict(r=..., w=...)` でも同じものになりますが、利用者の設定ファイルを
        lint にかけると ruff の `C408`（`Unnecessary dict() call`）が出るので、
        例は波括弧で書いています。

        `name` は**書いていない向きすべての名前**です。`{"name": X}` は文字列の
        `X` と同じ結果になり、1 つの向きだけ別の名前にする書き方ができます。

            0xE000: {"name": "Port", "w": "Latch"},   # 読みと即値は Port

        `comment` は `EQU` 行に付ける説明です（`equ_comments` に分けて持ちます）。
        1 行なら最初の `EQU` 行の末尾に、複数行ならその行の前に出ます。

            0xC0F4: {"name": "GameFlags", "comment": "bit0=一時停止中 bit1=デモ中"},

        Args:
            value (dict | None): `{アドレス: 名前 | dict}`。

        Raises:
            ValueError: 名前に使えない文字、未知のキー、空の指定、名前の無い
                指定、空の `comment` の場合。
        """
        parsed = {}
        comments = {}
        for key, spec in (value or {}).items():
            names = {"r": spec, "w": spec} if isinstance(spec, str) else spec
            if not isinstance(names, dict) or not names:
                raise ValueError(
                    f"Invalid equ entry for address {key}: {spec!r} "
                    f'(use a name, or {{"name": ..., "r": ..., "w": ..., "imm": ...}})'
                )
            unknown = set(names) - set(_EQU_KEYS)
            if unknown:
                raise ValueError(
                    f"Invalid equ keys for address {key}: {sorted(unknown)} "
                    f"(use {' / '.join(_EQU_KEYS)})"
                )
            names = dict(names)
            has_comment = "comment" in names
            comment = names.pop("comment", None)
            default = names.pop("name", None)
            # `name` は書いていない向きすべての名前。即値も埋めるので、
            # `{"name": "Port", "r": "In"}` の即値は `In` ではなく `Port` になる。
            checked = list(names.values()) + ([default] if default is not None else [])
            if default is not None:
                for mode in _EQU_MODES:
                    names.setdefault(mode, default)
            if not names:
                raise ValueError(
                    f"Invalid equ entry for address {key}: {spec!r} needs a name"
                )
            for name in checked:
                if not isinstance(name, str) or not _RE_LABEL_NAME.match(name):
                    raise ValueError(
                        f"Invalid label name for address {key}: {name!r} "
                        f"(use letters, digits, '_' and '.')"
                    )
            addr = parse_entry(key)
            parsed[addr] = names
            if has_comment:
                _check_comment_text("equ", key, comment)
                comments[addr] = comment
        self._equ_names = parsed
        self._equ_comments = comments

    def _equ_rows(self):
        """`EQU` 定義行を作ります（出力の先頭に置く）。

        同じアドレスに違う名前があるだけ行を出します（`r` / `w` / `imm` で最大
        3 行）。pz80 では値が同じ `EQU` を別名で定義できます（重複検査は名前に
        対して行うため）。`imm` に `w` と同じ名前を書くのが普通の使い方なので、
        その場合は 1 行にまとまります。

        Returns:
            list[dict]: `{"asm": "NAME: EQU 0xXXXX"}` の並び。アドレス順。
        """
        rows = []
        for addr in sorted(self._equ_names):
            seen = []
            own = []
            for mode in _EQU_MODES:
                name = self._equ_names[addr].get(mode)
                if name and name not in seen:
                    seen.append(name)
                    own.append({"asm": f"{name}: EQU 0x{addr:04X}"})
            rows += self._with_equ_comment(own, self._equ_comments.get(addr))
        return rows

    @staticmethod
    def _with_equ_comment(own, comment):
        """1 つのアドレスの `EQU` 行に `comment` を付けます。

        付けるのは**最初の行だけ**です（読み → 書き → 即値の順で最初に出る行）。
        1 行の説明はその行の末尾に、複数行の説明はその行の前に出します。
        `EQU` 行は出力の先頭にまとめて並ぶので、1 行なら行末に置いたほうが
        名前の一覧として読みやすいためです。

        Args:
            own (list[dict]): そのアドレスの `EQU` 行（1〜3 行）。
            comment (str | None): 説明。

        Returns:
            list[dict]: 説明を付けた行の並び。
        """
        if not comment:
            return own
        if "\n" not in comment:
            own[0]["asm"] = f"{own[0]['asm']} ; {comment}"
            return own
        return _block_rows(comment) + own

    @property
    def equ_comments(self):
        """`equ` の `comment` で書いた説明 `{アドレス: 文字列}`（読み取り専用）。

        `equ_names` は `{アドレス: {"r": …, "w": …}}` の形を保つので、説明は
        ここに分けて持っています。

        Returns:
            dict: キーは int に解決済み。
        """
        return self._equ_comments

    def _equ_text(self, addr, mode):
        """`equ` で付けた名前を返します。無ければ None。

        探す順は `_EQU_FALLBACK` にあります。`mode` が示す向きの名前を優先し、
        無ければ他を使います。即値（`LD hl, nn`）はアドレスを読むとも書くとも
        決まらないので、専用の `imm` があればそれを最優先します。

        Args:
            addr (int): 対象アドレス。
            mode (str): "r"（読み） / "w"（書き） / "imm"（即値）。

        Returns:
            str | None: 名前。
        """
        names = self._equ_names.get(addr)
        if not names:
            return None
        for key in _EQU_FALLBACK.get(mode, _EQU_MODES):
            if names.get(key):
                return names[key]
        return None

    @property
    def label_names(self):
        """ラベルに添える名前 `{アドレス: 名前}`。

        Returns:
            dict: キーは int に解決済み。
        """
        return self._label_names

    @label_names.setter
    def label_names(self, value):
        """名前を検証して設定します。

        キーは `parse_entry()` で int に解決するので、`"NMI"` や `"0x0066"` も
        書けます。名前は**トークナイザが 1 トークンとして扱える綴り**に限ります。
        `,` や `(` を含む名前を通すと、出力は一見正しく見えるのに
        **再アセンブルできない**状態になり、気づくのが遅れます。

        値は文字列か、`name` / `imm` / `comment` を持つ dict です。

            labels = {
                0x1200: "MsgTable",                          # 今までどおり
                0x0020: {"name": "Rst20", "imm": False},     # 即値には使わない
                0x0300: {"name": "PlaySound", "comment": "効果音を鳴らす"},
            }

        `comment` はラベル行の前に出す説明です（`label_comments` に分けて
        持ちます）。`\\n` ごとに 1 行です。同じアドレスに `comments` の `block` も
        あるときは、`block` → `comment` → ラベル行の順に並びます。

        **`imm` を False にすると、16 ビット即値（`LD rr, nn`）の置き換えから
        外れます。** 間接参照（`LD a, (nn)`）と分岐（`JP` / `CALL` / `JR` /
        `DJNZ`）は今までどおり名前になり、そのアドレスの定義行にもラベルが付きます。

        RST ベクタのような**小さいアドレス**に名前を付けるときに要ります。`0x0008`
        や `0x0020` は転送バイト数や構造体の間隔としてもよく使われる値なので、
        名前を付けると定数まで置き換わります。`LD de, L_0020@Rst20` は
        「ルーチンのアドレスを DE に入れている」と読めてしまい、実際は間隔 0x20 です。

        Args:
            value (dict | None): `{アドレス: 名前 | dict}`。

        Raises:
            ValueError: 名前に使えない文字、未知のキー、`name` が無い dict、
                `imm` が bool でない、空の `comment` の場合。
        """
        parsed = {}
        no_imm = set()
        comments = {}
        for key, spec in (value or {}).items():
            if isinstance(spec, dict):
                unknown = set(spec) - set(_LABEL_KEYS)
                if unknown:
                    raise ValueError(
                        f"Invalid label keys for address {key}: {sorted(unknown)} "
                        f"(use {' / '.join(_LABEL_KEYS)})"
                    )
                if "name" not in spec:
                    raise ValueError(
                        f'Invalid label entry for address {key}: needs "name"'
                    )
                name = spec["name"]
                use_imm = spec.get("imm", True)
                # bool に限る。`"imm": "False"` のような書き間違いは真になって
                # しまい、指定したつもりで効かない状態に気づけない。
                if not isinstance(use_imm, bool):
                    raise ValueError(
                        f"Invalid label imm for address {key}: {use_imm!r} "
                        f"(use True or False)"
                    )
                if "comment" in spec:
                    _check_comment_text("label", key, spec["comment"])
            else:
                name, use_imm = spec, True
            if not isinstance(name, str) or not _RE_LABEL_NAME.match(name):
                raise ValueError(
                    f"Invalid label name for address {key}: {name!r} "
                    f"(use letters, digits, '_' and '.')"
                )
            addr = parse_entry(key)
            parsed[addr] = name
            if not use_imm:
                no_imm.add(addr)
            if isinstance(spec, dict) and "comment" in spec:
                comments[addr] = spec["comment"]
        self._label_names = parsed
        self._label_no_imm = no_imm
        self._label_comments = comments

    @property
    def label_no_imm(self):
        """16 ビット即値の置き換えに使わないアドレスの集合。

        `label_names` の値に `{"name": …, "imm": False}` と書いたアドレスが入ります。
        `label_names` は `{アドレス: 名前}` の形を保つので、旗はここに分けて
        持っています。

        Returns:
            set[int]: 該当するアドレス。
        """
        return self._label_no_imm

    @property
    def label_comments(self):
        """`label_names` の `comment` で書いた説明 `{アドレス: 文字列}`（読み取り専用）。

        `label_names` は `{アドレス: 名前}` の形を保つので、説明はここに分けて
        持っています。

        Returns:
            dict: キーは int に解決済み。
        """
        return self._label_comments

    @property
    def raw_operand(self):
        """16 ビットオペランドを数値のまま出す**命令のアドレス**の集合。

        `label_names` と `equ_names` の両方に対して効きます。同じ値が、ある場所
        ではアドレス・別の場所では定数、という混在を 1 か所ずつ潰すためのものです。
        `label_no_imm` はアドレスごと、こちらは**命令ごと**の指定になります。

        Returns:
            set[int]: 該当する命令のアドレス。
        """
        return self._raw_operand

    @raw_operand.setter
    def raw_operand(self, value):
        """命令のアドレスを検証して設定します。

        Args:
            value (iterable | None): 命令のアドレス。`parse_entry()` が解釈できる形。
        """
        self._raw_operand = {parse_entry(a) for a in (value or ())}

    @property
    def comments(self):
        """出力へ出すコメント `{アドレス: {"line": …, "block": …}}`。

        Returns:
            dict: キーは int に解決済み。文字列指定は `{"line": …}` に正規化される。
        """
        return self._comments

    @comments.setter
    def comments(self, value):
        """コメントを検証して設定します。

        **`labels` や `equ` では付けられない注釈のためにあります。** たとえば
        `LD a, 0x03` の `0x03` が曲番号だと分かっていても、アドレスではないので
        名前を付ける機構では扱えません。設定ファイル側のコメントは Python の
        コメントなので出力に出ず、出力へ手で書き足しても**作り直すと消えます**。

        値は文字列か、`line` / `block` を持つ dict です。

            comments = {
                0x0120: "曲番号 3",                     # 行末に出す
                0x0200: {"block": "サウンドドライバ\\n毎フレーム NMI から呼ぶ"},
                0x0350: {"line": "効果音 1", "block": "爆発音"},
            }

        `line` はその行の末尾へ `; …` として付きます。`block` はその行の**前**に
        `; …` の行として出ます（`\\n` ごとに 1 行）。ラベルがあるアドレスでは
        **ラベル行より前**に出るので、「ここから何が始まるか」の見出しになります。

        Args:
            value (dict | None): `{アドレス: 文字列 | dict}`。

        Raises:
            ValueError: 未知のキー、空の指定、`line` に改行が含まれる場合。
        """
        parsed = {}
        for key, spec in (value or {}).items():
            if isinstance(spec, str):
                spec = {"line": spec}
            if not isinstance(spec, dict) or not spec:
                raise ValueError(
                    f"Invalid comment for address {key}: {spec!r} "
                    f'(use a string, or {{"line": ..., "block": ...}})'
                )
            unknown = set(spec) - set(_COMMENT_KEYS)
            if unknown:
                raise ValueError(
                    f"Invalid comment keys for address {key}: {sorted(unknown)} "
                    f"(use {' / '.join(_COMMENT_KEYS)})"
                )
            for name, text in spec.items():
                if not isinstance(text, str) or not text.strip():
                    raise ValueError(
                        f"Invalid comment {name} for address {key}: {text!r} "
                        f"(use a non-empty string)"
                    )
                # 行末コメントに改行が入ると、その行だけでは閉じない出力になる。
                # 複数行を出したいときは `block` を使う。
                if name == "line" and "\n" in text:
                    raise ValueError(
                        f"Invalid comment line for address {key}: contains a newline "
                        f"(use block for multiple lines)"
                    )
            parsed[parse_entry(key)] = dict(spec)
        self._comments = parsed

    def _label_text(self, addr):
        """アドレスに対応するラベル文字列を返します。

        名前が登録されていれば `L_0066@NMI`、無ければ従来どおり `L_0066`。

        **住所を名前に残すのが要点**です。効いている理由は 2 つあり、
        重いのは後者です。

        1. `disasm` が参照と定義を**レンダリング済みテキスト経由で**突き合わせる
           （`_attach_labels` が `_re_label` で住所を読み戻す）
        2. **名前の一意性を住所が保証している。** `label_names` は同じ名前を
           別のアドレスに付けられてしまうが、`L_1250@Str` と `L_1370@Str` に
           分かれるので衝突しない。住所を捨てると `Duplicate label definition`
           で**再アセンブルできなくなる**

        `walk` と `_auto_entry` も `L_xxxx` から住所を読みます。**`L_xxxx` の形を
        やめる案**（`L_0066` → `L_@NMI`）ならあちらが壊れます。一方**名前付きの
        ときだけ接頭辞を落とす案**では、あちらに `@名前` が届かないので無関係です
        （`build_addr_map()` / `sweep_from()` が作る `Disasm` は `label_names`
        未設定）。受け側の `(?:@…)?` は防御的な記述です。

        **案の範囲で拘束条件が変わります。** 後者は 2026-09-23 に測って見送り
        ました。

        なお**アセンブラはこの名前を解釈しません**。`L_0066@NMI` は
        不透明な識別子として扱われ、値は定義行の位置で決まります。手を入れて
        アドレスがずれても名前が古くなるだけで、アセンブル結果は常に正しくなります。

        Args:
            addr (int): 対象アドレス。

        Returns:
            str: ラベル文字列（コロンは付けない）。
        """
        name = self.label_names.get(addr)
        return f"L_{addr:04X}@{name}" if name else f"L_{addr:04X}"

    @staticmethod
    def _word_mode(asm):
        """16 ビットオペランドが読みか書きか即値かを判定します。

        `equ` で read と write に別の名前を付けられるようにするため、命令の形から
        向きを読みます。22 命令は綺麗に割れます（書き 7・読み 7・即値 6）。

            ['ld','(','0x{1}{0}',')',',','a']   -> "w"   (nn) が左辺
            ['ld','a',',','(','0x{1}{0}',')']   -> "r"   (nn) が右辺
            ['ld','hl',',','0x{1}{0}']          -> "imm" 括弧が無い

        Args:
            asm (list): 命令表の `asm` トークン列。

        Returns:
            str: "r" / "w" / "imm"。
        """
        i = asm.index("0x{1}{0}")
        if asm[i - 1] != "(":
            return "imm"
        return "r" if "," in asm[:i] else "w"

    def _word_operand(self, tmpl, lo, hi, mode="imm", site=None):
        """16 ビットのアドレスオペランドを描画します。

        分岐命令以外（`LD de, nn` / `LD a, (nn)` など 22 命令）の 16 ビット即値は、
        **既定では数値のまま**出します。アドレスとは限らないからです。`LD bc, 0x0100`
        はカウンタの初期値かもしれず、`LD hl, 0x4000` は VRAM のベースかもしれない。
        逆アセンブラには区別が付きません。

        置き換えるのは `label_names` で**名前を付けたアドレスだけ**にしています。
        名前を付けた時点で利用者が「ここは意味のあるアドレスだ」と宣言しているので、
        誤爆しません。`label_addresses`（`entry` 由来）は対象外です。そちらは
        名前が無く、`entry` にありがちな `0x0000` まで巻き込むと
        `LD hl, 0` のような定数まで置き換わってしまいます。

        `equ` で付けた名前は**裸の名前**（`MirrorRam`）、`labels` で付けた名前は
        `L_xxxx@名前` になります。前者は逆アセンブル範囲外の定数で定義行が
        `EQU` として先頭に出るため、住所を名前に残す必要がありません。

        **名前を付けたアドレスでも、即値では使わない指定ができます**
        （`labels` の `{"name": …, "imm": False}`）。RST ベクタのような小さいアドレスに
        名前を付けると、同じ値の定数まで置き換わるためです。間接参照と分岐は
        オペランドが確実にアドレスなので、この指定でも名前のままにします。

        `raw_operand` に**命令のアドレス**を書くと、その命令だけ数値のまま出します。
        同じ値が、ある場所ではアドレス・別の場所では定数、という混在を潰すためです。

        Args:
            tmpl (str): `_tmpl()` が返したテンプレート文字列。
            lo (int): アドレス下位バイト。
            hi (int): アドレス上位バイト。
            mode (str): "r" / "w" / "imm"。`equ` の読み名・書き名の選択に使う。
            site (int | None): この命令のアドレス。`raw_operand` の判定に使う。

        Returns:
            str: 描画後のアセンブリ文字列。
        """
        raw = tmpl.replace("{0}", "{0:02X}").replace("{1}", "{1:02X}").format(lo, hi)
        if site is not None and site in self._raw_operand:
            return raw

        addr = (hi << 8) | lo
        equ = self._equ_text(addr, mode)
        if equ:
            return tmpl.replace("0x{1}{0}", equ)
        if addr in self.label_names:
            if mode == "imm" and addr in self._label_no_imm:
                return raw
            return tmpl.replace("0x{1}{0}", self._label_text(addr))
        return raw

    def _m1_decode(self, adr, raw_bytes):
        """M1サイクル対象バイトにハンドラーを適用する。

        Z80仕様: 2バイトオペコード (CB/DD/ED/FD で始まる命令) では
        各オペコードバイトのフェッチ時に M1 が生成される。
          - バイト0: 常にM1
          - バイト1: バイト0がプレフィックス (DD/FD/ED/CB) の場合のみM1
          - DDCB/FDCB (4バイト命令): バイト2(displacement)・バイト3(op)は
            オペコードではなくオペランドのため M1 なし

        Args:
            adr (int): 命令の先頭アドレス。
            raw_bytes (list[int]): メモリから読んだ生バイト列。

        Returns:
            list[int]: M1ハンドラー適用後のバイト列。
        """
        if not self.m1_handler or not raw_bytes:
            return raw_bytes

        result = list(raw_bytes)

        # バイト0: 常にM1
        result[0] = self.m1_handler(adr, result[0])
        b0 = result[0]

        if len(result) < 2:
            return result

        # バイト1: プレフィックスバイトの場合のみM1
        if b0 in _Z80_PREFIXES:
            result[1] = self.m1_handler(adr + 1, result[1])

        return result

    @property
    def datamap(self):
        """データマッププロパティ。

        Returns:
            list: データ範囲のリスト [[開始, 終了], ...]。
        """
        return self._datamap

    @datamap.setter
    def datamap(self, p):
        """データ範囲を検証して設定します。

        要素は `[開始, 終了]`（両端含む・今までどおり全バイト `db`）か、
        `range` / `fmt` / `per_line` を持つ dict です。

            data = [
                [0x1000, 0x10FF],                              # 全部 db
                {"range": [0x1100, 0x113F], "fmt": "b w b"},   # db, dw, db の繰り返し
                {"range": [0x1140, 0x1149], "fmt": "w"},       # dw の並び
                {"range": [0x1200, 0x12FF], "fmt": "b", "per_line": 8},
            ]

        **`fmt` は範囲の先頭から繰り返す型の並びです。** `w` はリトルエンディアンの
        2 バイトを `dw` 1 行にまとめ、その値が `equ` や `labels` のアドレスと一致すれば
        名前に置き換えます。一致しなければ `dw 0x1200` と数値で出します（表の中には
        名前を付けていないアドレスが普通にあるので、ここで警告は出しません）。

        `per_line` は `db` を 1 行にまとめるバイト数です。音符のような長いバイト列を
        読むための指定なので、**まとめた行には `; [文字]` の注釈を出しません**。

        `datamap` が返すのは今までどおり `[[開始, 終了], ...]` です。書式は別に
        持っているので、範囲だけを見ている既存の呼び出し側は影響を受けません。

        Args:
            p (list | None): データ範囲のリスト。

        Raises:
            ValueError: 範囲の形式が不正、未知のキー、`range` が無い dict、
                `fmt` に `b` / `w` 以外が入る、`per_line` が正の整数でない場合。
        """
        ranges = []
        fmts = []
        for entry in p or []:
            rng, tokens, per_line = self._parse_data_entry(entry)
            ranges.append(rng)
            if tokens or per_line:
                fmts.append((rng[0], rng[1], tokens, per_line))
        self._datamap = ranges
        self._data_fmt = fmts

    @staticmethod
    def _parse_data_entry(entry):
        """`data` の 1 要素を `([開始, 終了], 型の並び, 1行のバイト数)` に直します。

        開始 > 終了 はここでは弾きません。CLI が今までどおり自分の文面で報告する
        ため（`Error: Invalid data range in config`）、検査の場所を動かしません。

        Args:
            entry (list | tuple | dict): `data` の 1 要素。

        Returns:
            tuple: `([開始, 終了], tuple | None, int | None)`。

        Raises:
            ValueError: 形式が不正な場合。
        """
        fmt = per_line = None
        if isinstance(entry, dict):
            unknown = set(entry) - set(_DATA_KEYS)
            if unknown:
                raise ValueError(
                    f"Invalid data keys: {sorted(unknown)} "
                    f"(use {' / '.join(_DATA_KEYS)})"
                )
            if "range" not in entry:
                raise ValueError(f'Invalid data entry: {entry!r} needs "range"')
            rng = entry["range"]
            fmt = entry.get("fmt")
            per_line = entry.get("per_line")
        else:
            rng = entry

        try:
            lo, hi = rng
        except (TypeError, ValueError):
            raise ValueError(
                f"Invalid data range: {rng!r} (use [start, end])"
            ) from None
        if not isinstance(lo, int) or not isinstance(hi, int):
            raise ValueError(f"Invalid data range: {rng!r} (use [start, end])")

        where = f"0x{lo:04X}-0x{hi:04X}"
        tokens = None
        if fmt is not None:
            tokens = tuple(fmt.split()) if isinstance(fmt, str) else ()
            if not tokens or any(t not in _DATA_FMT_SIZES for t in tokens):
                raise ValueError(
                    f"Invalid data fmt for {where}: {fmt!r} "
                    f"(use {' / '.join(_DATA_FMT_SIZES)} separated by spaces)"
                )
        # bool は int なので明示的に外す。`per_line: True` は書き間違いだが、
        # 通すと 1 バイトずつまとめる指定として黙って効いてしまう。
        if per_line is not None and (
            isinstance(per_line, bool) or not isinstance(per_line, int) or per_line < 1
        ):
            raise ValueError(
                f"Invalid data per_line for {where}: {per_line!r} "
                f"(use a positive integer)"
            )
        return [lo, hi], tokens, per_line

    def _check_data_fmt(self):
        """`fmt` で範囲を割り切れない指定を `warnings` に記録します。

        **止めません。** 表の終端の見積もりが 1 要素ずれることは解析中によくあり、
        そこで出力そのものが得られなくなると、ずれを直すのに使う出力が無くなります。
        残りは `db` で出すので、再アセンブルできる出力は必ず得られます。
        """
        for lo, hi, tokens, _per_line in self._data_fmt:
            if not tokens:
                continue
            cycle = sum(_DATA_FMT_SIZES[t] for t in tokens)
            rest = (hi - lo + 1) % cycle
            if rest:
                self.warnings.append(
                    f'data fmt "{" ".join(tokens)}" does not fit '
                    f"0x{lo:04X}-0x{hi:04X}: {rest} trailing "
                    f"byte{'s' if rest > 1 else ''} emitted as db"
                )

    @property
    def valid_ranges(self):
        """バイナリが実在するアドレス範囲 `[[開始, 終了], ...]`。

        Returns:
            list[list[int]] | None: 昇順・重なり無しに正規化した範囲。
                None なら全域を走査する（従来どおり）。
        """
        return self._valid_ranges

    @valid_ranges.setter
    def valid_ranges(self, value):
        """走査するアドレス範囲を設定します。**隙間は出力から消えます**。

        `bins` で複数ファイルを別々のアドレスに置くと、ファイルとファイルの間に
        **どのファイルも置かれていないアドレス**ができます。イメージ上はそこが
        `0x00` で埋まっているので、指定しないと `nop` の列として逆アセンブル
        されます。実在しないバイトを命令として読んでいるわけで、`walk` は
        同じ隙間を最初から除外しています。ここを揃えるための設定です。

        隙間を挟むたびに `org` を出し直すので、出力はそのまま再アセンブルできます。
        命令の復号も区間の終端で止まるため、**隙間のバイトを巻き込んだ命令**が
        できることもありません。

        Args:
            value (list | None): `[[開始, 終了], ...]`（両端含む）。None で全域。

        Raises:
            ValueError: 範囲の形式が不正、開始 > 終了、
                0x0000-0xFFFF の外、または空リストの場合。
        """
        if value is None:
            self._valid_ranges = None
            return

        ranges = []
        for r in value:
            try:
                lo, hi = r
            except (TypeError, ValueError):
                raise ValueError(
                    f"Invalid valid_ranges entry: {r!r} (use [start, end])"
                ) from None
            if lo > hi:
                raise ValueError(
                    f"Invalid valid_ranges entry: start=0x{lo:04X} > end=0x{hi:04X}"
                )
            if lo < 0 or hi > 0xFFFF:
                raise ValueError(
                    f"valid_ranges entry outside the Z80 address space: "
                    f"0x{lo:04X}-0x{hi:04X}"
                )
            ranges.append([lo, hi])

        if not ranges:
            raise ValueError(
                "valid_ranges is empty (use None to disassemble the whole image)"
            )

        # 重なりと隣接をまとめる。隣接を繋ぐのは、続きのファイルの境目で
        # `org` を出し直しても意味が無いため（出力が無駄に切れるだけ）。
        ranges.sort()
        merged = [ranges[0]]
        for lo, hi in ranges[1:]:
            if lo <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])
        self._valid_ranges = merged

    def _segments(self, start, end):
        """走査する区間を返します。`valid_ranges` の隙間はここで落ちます。

        Args:
            start (int): 逆アセンブル開始アドレス。
            end (int): 逆アセンブル終了アドレス（両端含む）。

        Returns:
            list[list[int]]: `[[開始, 終了], ...]`。昇順・重なり無し。
        """
        if self._valid_ranges is None:
            return [[start, end]]
        return [
            [max(lo, start), min(hi, end)]
            for lo, hi in self._valid_ranges
            if max(lo, start) <= min(hi, end)
        ]

    def op2asm(self, adr, opcode):
        """オペコードをアセンブリ文字列に変換します。

        Args:
            adr (int): 現在のアドレス。
            opcode (list): オペコードバイト列 (1〜4バイト)。
                例: [0x3E, 0x10]  (LD A, 0x10)
                    [0xDD, 0x21, 0x00, 0x10]  (LD IX, 0x1000)

        Returns:
            str: アセンブリ文字列、または一致しない場合はNone。
                例: "LD A, 10"
        """
        # ------------------------------------------------------
        # ここからメイン
        # ------------------------------------------------------
        if any(p[0] <= adr <= p[1] for p in self.datamap):
            if len(opcode) != 1:
                return None  # 1バイト単位で処理させるため他の長さは不一致扱い
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
        if u["bytes"] != len(opcode):
            return None
        handler = self._dispatch.get(u["bytes"])
        return handler(u, opcode, adr) if handler else None

    def exec(self, start, images, size):
        """逆アセンブルを実行します。

        Args:
            start (int): 開始アドレス。
            images (list): バイナリイメージデータ (startアドレスからのデータ列)。
            size (int): データサイズ。

        Returns:
            list: 逆アセンブルされた行のリスト。各要素は以下のいずれかの形式。
                ORG行:  {"address": int, "asm": str}
                命令行: {"address": int, "opcode": list, "asm": str}
                ラベル付き命令行: {"address": int, "opcode": list, "asm": str, "label": str}
                例: [{"address": 0x100, "asm": "org 0x0100"},
                     {"address": 0x100, "opcode": [0x3E, 0x10], "asm": "LD A, 10"},
                     {"address": 0x102, "opcode": [0xC3, 0x00, 0x01],
                      "asm": "JP L_0100", "label": "L_0100:"}]
        """
        maxword = 0xFFFF

        if size + start > maxword:
            return []
        if (start > maxword) or (start < 0):
            return []

        mem = [0] * 0x10000
        mem[start : start + size] = images[:size]
        end = start + size - 1

        segments = self._segments(start, end)
        if not segments:
            raise ValueError(
                f"valid_ranges does not overlap the disassembled range "
                f"0x{start:04X}-0x{end:04X} (nothing to disassemble)"
            )
        self._check_label_names_in_range(segments)
        self.warnings = []
        self._check_data_fmt()

        # 区間ごとに `org` を出し直す。隙間を飛ばしたままアドレスを進めないと、
        # 再アセンブルしたときに後続が隙間の分だけ手前へ詰まる。
        lst = []
        for seg_start, seg_end in segments:
            lst.append({"address": seg_start, "asm": f"org 0x{seg_start:04X}"})
            lst += self._scan(mem, seg_start, seg_end)

        dangling = self._attach_labels(lst)
        # `per_line` のまとめも `_attach_labels` の後。ラベルの付いた行で切るので、
        # 参照から見つかったラベルまで含めて 1 行の途中に埋もれない。
        lst = self._group_data_lines(lst)
        # コメントは `_attach_labels` の後。アドレスを持たない行を混ぜるので索引作りの
        # 後でなければならず、`block` をラベルより前に出すためにもこの順が要る。
        lst = self._apply_comments(lst, segments)
        # EQU 行は `_attach_labels` の後で足す。あちらは `item["address"]` で
        # 索引を作るので、アドレスを持たない行を混ぜない。
        return self._equ_rows() + self._dangling_rows(dangling) + lst

    def _check_label_names_in_range(self, segments):
        """`label_names` が逆アセンブル範囲内を指しているか検査します。

        `labels` は「その行にラベルを貼る」機構なので、範囲外のアドレスには
        **定義行を置く場所がありません**。それでも参照側は
        `LD hl, L_8000@MirrorRam` と名前で出るため、出力は一見正しく見えるのに
        `Undefined symbol` で再アセンブルできない状態になります。

        RAM や I/O のように範囲外を指す定数は `equ` の仕事です。`valid_ranges`
        の隙間（`bins` でどのファイルも置かれていないアドレス）も同じ扱いになります。

        Args:
            segments (list[list[int]]): 走査する区間 `[[開始, 終了], ...]`。

        Raises:
            ValueError: どの区間にも入らないアドレスに名前が付いている場合。
        """
        outside = sorted(
            a
            for a in self._label_names
            if not any(lo <= a <= hi for lo, hi in segments)
        )
        if outside:
            listed = ", ".join(f"0x{a:04X}" for a in outside)
            where = ", ".join(f"0x{lo:04X}-0x{hi:04X}" for lo, hi in segments)
            raise ValueError(
                f"labels outside the disassembled range {where}: {listed} "
                f"(use equ for RAM / I/O addresses)"
            )

    def _apply_comments(self, lst, segments):
        """`comments` を行へ反映した新しいリストを返します。

        `line` は行の `asm` の末尾へ足し、`block` は**その行の前**にアドレスを持たない
        行として挿し込みます。データ行にはすでに `; [文字]` の注釈が付いているので、
        `db 0x04 ; [.] ; 速度` と並びます（文字注釈は落としません。
        文字列データで役に立つため）。

        `_attach_labels()` の**後**に呼んでください。あちらは `item["address"]` で
        索引を作るので、アドレスを持たない行を先に混ぜると辻褄が合いません。
        後に呼ぶことで、`block` がラベルより前に出る形にもなります。

        Args:
            lst (list[dict]): 命令行・データ行のリスト。
            segments (list[list[int]]): 走査した区間。

        Returns:
            list[dict]: コメントを反映したリスト。

        Raises:
            ValueError: 範囲外、または命令の途中を指すアドレスがある場合。
        """
        if not self._comments and not self._label_comments:
            return lst

        # 同じアドレスに `org` 行と命令行が並ぶので、`opcode` を持つ方を採る。
        rows = {}
        for item in lst:
            if "address" in item and "opcode" in item:
                rows.setdefault(item["address"], item)
        self._check_comment_addresses(rows, segments)

        out = []
        for item in lst:
            is_row = "address" in item and "opcode" in item
            spec = self._comments.get(item["address"]) if is_row else None
            if spec:
                out += _block_rows(spec.get("block"))
                if spec.get("line"):
                    item["asm"] = f"{item.get('asm', '')} ; {spec['line']}"
            # `labels` の `comment` は `comments` の `block` の後、ラベル行の直前。
            # `block` を節の見出しに使っている設定があるため（依頼元の指定）。
            if is_row and "label" in item:
                out += _block_rows(self._label_comments.get(item["address"]))
            out.append(item)
        return out

    def _check_comment_addresses(self, rows, segments):
        """`comments` のアドレスが行に貼れるかを検査します。

        **警告ではなく止めます。** `labels` の範囲外検査と同じ扱いにしてあります。
        同じ種類の間違いなのに機構によって扱いが変わると、どちらの規則だったかを
        毎回思い出す必要が出てきます。設定ファイルは直すたびに作り直して試すので、
        止まってもすぐ直せます。再生成のたびに流れるログへ警告を出すと埋もれます。

        Args:
            rows (dict[int, dict]): アドレスをキーにした命令行・データ行。
            segments (list[list[int]]): 走査した区間。

        Raises:
            ValueError: 範囲外、または命令の途中を指すアドレスがある場合。
        """
        for addr in sorted(self._comments):
            if not any(lo <= addr <= hi for lo, hi in segments):
                where = ", ".join(f"0x{lo:04X}-0x{hi:04X}" for lo, hi in segments)
                raise ValueError(
                    f"comment outside the disassembled range {where}: 0x{addr:04X}"
                )
            if addr in rows:
                continue
            head = self._containing_head(rows, addr)
            if head is None:
                raise ValueError(f"comment at 0x{addr:04X} has no line to attach to")
            # `fmt` の `w` は 2 バイトを 1 行にするので、上位バイトを指すと
            # ここに来る。「命令の途中」と書くと嘘になるのでニーモニックで言う。
            mnemonic = rows[head].get("asm", "").split(" ", 1)[0].lower()
            kind = mnemonic if mnemonic in _DATA_FMT_DIRECTIVES else "instruction"
            raise ValueError(
                f"comment at 0x{addr:04X} is inside the {kind} at 0x{head:04X}"
            )

    @staticmethod
    def _containing_head(rows, addr):
        """`addr` を含む命令の先頭アドレスを返します。無ければ None。

        エラーの文面に出すためのものです。「命令の途中を指している」だけでは
        どこを直せばよいか分からないので、直すべきアドレスを添えます。

        Args:
            rows (dict[int, dict]): アドレスをキーにした命令行・データ行。
            addr (int): 対象のアドレス。

        Returns:
            int | None: 含んでいる命令の先頭アドレス。
        """
        for head in sorted((a for a in rows if a < addr), reverse=True):
            if head + len(rows[head].get("opcode", [])) > addr:
                return head
            break  # 直前の命令で届かないなら、それより前でも届かない
        return None

    def _dangling_rows(self, dangling):
        """定義行を置けなかったラベルを `EQU` で定義する行を作ります。

        `JP L_8000` のように**行の無いアドレス**を指す参照は前からありました。
        RAM へ飛ぶもの、命令の途中を指すもの、そして `valid_ranges` の隙間を
        指すものです。参照だけ出て定義が無いので、出力は
        `Undefined symbol 'L_8000'` で再アセンブルできませんでした。

        値が決まっている以上ここで `EQU` にしてしまえます。`equ` で利用者が
        付けた名前とは別の行になりますが、pz80 は値の同じ `EQU` を別名で
        定義できるので衝突しません。

        `labels` で命令の途中に名前を付けた場合もここに来ます。その `comment` は
        この `EQU` 行の前に出します。ラベルが出る場所に説明も出す形にそろえるため
        （依頼元の判断。エラーにすると、ラベルは書けるのに説明は書けなくなる）。

        Args:
            dangling (dict): `{アドレス: ラベル名}`（コロンなし）。

        Returns:
            list[dict]: `{"asm": "L_8000: EQU 0x8000"}` の並び。アドレス順。
        """
        rows = []
        for addr, name in sorted(dangling.items()):
            rows += _block_rows(self._label_comments.get(addr))
            rows.append({"asm": f"{name}: EQU 0x{addr:04X}"})
        return rows

    def _scan(self, mem, start, end):
        """指定範囲を走査して命令行・データ行のリストを返します。

        Args:
            mem (list): 64KB のメモリイメージ。
            start (int): 走査開始アドレス。
            end (int): 走査終了アドレス（両端含む）。

        Returns:
            list: 命令行またはデータ行の辞書リスト。
        """
        lst = []
        adr = start
        while adr <= end:
            # データ領域はオペコードフェッチではないため M1 復号せず生バイトで出力する
            if any(r[0] <= adr <= r[1] for r in self.datamap):
                row, step = self._data_row(mem, adr, end)
                lst.append(row)
                adr += step
                continue

            opcode, asm = self._decode_at(mem, adr, end)
            if opcode is None:
                # どの長さでもマッチしなかった場合、1バイトのデータとして処理
                lst.append(
                    {
                        "address": adr,
                        "opcode": [mem[adr]],
                        "asm": f"db 0x{mem[adr]:02X} ; Invalid Opcode",
                    }
                )
                adr += 1
                continue

            lst.append({"address": adr, "opcode": opcode, "asm": asm})
            adr += len(opcode)
        return lst

    def _data_row(self, mem, adr, end):
        """データ領域の 1 行を作ります。`(行, 進めるバイト数)` を返します。

        `fmt` で `w` が来ている位置では 2 バイトを `dw` 1 行にします。それ以外は
        今までどおり 1 バイトの `db` に `; [文字]` を添えます。

        Args:
            mem (list): 64KB のメモリイメージ。
            adr (int): 対象アドレス。
            end (int): 走査範囲の終端（これを越えるバイトは読まない）。

        Returns:
            tuple[dict, int]: 行と消費バイト数（1 か 2）。
        """
        entry = self._data_fmt_at(adr)
        if entry is not None:
            lo, hi, tokens, _per_line = entry
            if self._fmt_kind(adr - lo, tokens, hi - lo + 1) == "w":
                if adr + 1 <= min(hi, end):
                    return {
                        "address": adr,
                        "opcode": [mem[adr], mem[adr + 1]],
                        "asm": self._dw_text(mem[adr], mem[adr + 1]),
                    }, 2
                # 区間の終端で切られると 2 バイト読めない。`valid_ranges` の隙間や
                # イメージの末尾に表の終端を重ねた場合で、`db` に落として続ける。
                self.warnings.append(
                    f"data fmt w at 0x{adr:04X} needs 2 bytes "
                    f"but the range ends at 0x{min(hi, end):04X}: emitted as db"
                )

        raw = mem[adr]
        return {
            "address": adr,
            "opcode": [raw],
            "asm": f"db 0x{raw:02X} ; [{self.cpu.strmap[raw]}]",
        }, 1

    def _data_fmt_at(self, adr):
        """`adr` を含む書式付きの `data` 要素を返します。無ければ None。

        Args:
            adr (int): 対象アドレス。

        Returns:
            tuple | None: `(開始, 終了, 型の並び, 1行のバイト数)`。
        """
        for entry in self._data_fmt:
            if entry[2] and entry[0] <= adr <= entry[1]:
                return entry
        return None

    @staticmethod
    def _fmt_kind(offset, tokens, size):
        """範囲の先頭から `offset` バイト目に来る型を返します。

        型の並びは範囲の先頭から繰り返します。**割り切れない残りは `db`** に
        します（`_check_data_fmt()` が警告を出す）。区間で切られて型の境目に
        乗っていない位置も `db` にします。

        Args:
            offset (int): 範囲の先頭からのバイト数。
            tokens (tuple): 型の並び（`("b", "w", "b")` など）。
            size (int): 範囲のバイト数。

        Returns:
            str: "b" または "w"。
        """
        cycle = sum(_DATA_FMT_SIZES[t] for t in tokens)
        if offset >= size - size % cycle:
            return "b"
        pos = offset % cycle
        at = 0
        for token in tokens:
            if at == pos:
                return token
            at += _DATA_FMT_SIZES[token]
        return "b"

    def _dw_text(self, lo, hi):
        """`dw` 行の文字列を作ります。名前が付いていればそれを使います。

        探す順は `equ` → `labels` → 数値です。**一致しなければ数値のまま**出します。
        表の中には名前を付けていないアドレスが普通にあり（スタックのアドレスなど）、
        そこで警告を出すと正しい指定でも警告が並びます。`fmt` を書いた本人が
        出力を見れば分かる、というのが依頼元の判断でした。

        `labels` の `imm: False` はここに効きません。あれは `LD rr, nn` の即値が
        定数かもしれない場合の指定で、**表の中の `dw` はアドレスそのもの**です。

        Args:
            lo (int): 下位バイト。
            hi (int): 上位バイト。

        Returns:
            str: `dw L_1200@Song0` / `dw SndVolume` / `dw 0x1200`。
        """
        addr = (hi << 8) | lo
        name = self._equ_text(addr, "imm")
        if not name and addr in self.label_names:
            name = self._label_text(addr)
        return f"dw {name}" if name else f"dw 0x{addr:04X}"

    def _group_data_lines(self, lst):
        """`per_line` の範囲で 1 バイトの `db` 行をまとめた新しいリストを返します。

        **`_attach_labels()` の後に呼んでください。** ラベルの付いた行で必ず切るので、
        参照から見つかったラベルも 1 行の途中に埋もれません。走査の中でまとめると
        参照側が見つかる前なので、この判断ができません。

        コメントを付けたアドレスでも切ります（依頼元の指定）。切った先が新しい行の
        先頭になるため、コメントは今までどおりその行に付きます。

        Args:
            lst (list[dict]): 命令行・データ行のリスト。

        Returns:
            list[dict]: `db` をまとめたリスト。まとめる指定が無ければそのまま。
        """
        if not any(entry[3] for entry in self._data_fmt):
            return lst

        out = []
        group = []  # まとめている行
        limit = 0  # まとめる上限。0 ならまとめていない
        for item in lst:
            per_line = self._per_line_of(item)
            if (
                group
                and per_line == limit
                and len(group) < limit
                and "label" not in item
                and item["address"] not in self._comments
            ):
                group.append(item)
                continue
            if group:
                out.append(self._merge_data_rows(group))
                group, limit = [], 0
            if per_line is None:
                out.append(item)
            else:
                group, limit = [item], per_line
        if group:
            out.append(self._merge_data_rows(group))
        return out

    def _per_line_of(self, item):
        """`item` が `per_line` 指定の 1 バイト `db` 行なら、そのバイト数を返します。

        `dw` 行（2 バイト）と `org` 行（`opcode` 無し）はここで外れるので、
        まとめる対象になりません。区間の隙間には必ず `org` 行が挟まるので、
        アドレスが飛んだ行同士がまとまることもありません。

        Args:
            item (dict): 行。

        Returns:
            int | None: 1 行にまとめるバイト数。対象外なら None。
        """
        if "address" not in item or len(item.get("opcode", ())) != 1:
            return None
        for lo, hi, _tokens, per_line in self._data_fmt:
            if per_line and lo <= item["address"] <= hi:
                return per_line
        return None

    @staticmethod
    def _merge_data_rows(group):
        """1 バイトの `db` 行をまとめて 1 行にします（先頭の行を書き換えます）。

        先頭の行に足していくので、そこに付いたラベルは残ります。**`; [文字]` の
        注釈は落とします**（依頼元の指定。音符のようなデータを読むための機能なので、
        まとめた行に文字注釈は要らない）。

        Args:
            group (list[dict]): まとめる行。先頭が新しい行になる。

        Returns:
            dict: まとめた行。
        """
        head = group[0]
        opcode = [row["opcode"][0] for row in group]
        head["opcode"] = opcode
        head["asm"] = "db " + ", ".join(f"0x{b:02X}" for b in opcode)
        return head

    def _decode_at(self, mem, adr, end):
        """1命令を復号します。最長の4バイトから順に1バイトまでマッチを試みます。

        Args:
            mem (list): 64KB のメモリイメージ。
            adr (int): 復号するアドレス。
            end (int): 走査範囲の終端（これを越えるバイトは読まない）。

        Returns:
            tuple[list | None, str | None]: (オペコード列, アセンブル文字列)。
                どの長さでもマッチしなければ (None, None)。
        """
        for length in (4, 3, 2, 1):
            if adr + length - 1 > end:
                continue
            opcode = self._m1_decode(adr, mem[adr : adr + length])
            asm = self.op2asm(adr, opcode)
            if asm:
                return opcode, asm
        return None, None

    def _attach_labels(self, lst):
        """逆アセンブル結果にラベルを付与します（この場で書き換えます）。

        コード中の `L_xxxx` 参照を集め、対応するアドレスの行に `label` を付けます。
        `label_addresses` と `label_names` で指定されたアドレスには、参照が無くても
        ラベルを付けます（NMI などコード中から参照されないエントリポイント、および
        `LD de, 0x0120` のようにジャンプ以外から指されるデータの先頭用）。

        **定義側は参照側の綴りをそのまま使います。** 正規表現が `@名前` ごと
        `group(1)` に含むので、参照が `CALL L_0240@DRAW` なら定義も
        `L_0240@DRAW:` になります。ここが食い違うと出力が再アセンブルできません。

        Args:
            lst (list): 逆アセンブル結果のリスト。

        Returns:
            dict: 貼る行が無かったラベル `{アドレス: 名前}`（コロンなし）。
                呼び出し側が `EQU` で定義する（`_dangling_rows()`）。
        """
        labels = {}
        for p in lst:
            m = self._re_label.search(p["asm"])
            if m:
                labels[int(m.group(2), 16)] = m.group(1) + ":"

        # 強制ラベル付与アドレス（NMI などコード中に参照のないエントリ、
        # および名前を付けたアドレス）
        for addr in list(self.label_addresses) + list(self.label_names):
            labels.setdefault(addr, self._label_text(addr) + ":")

        # アドレス検索用のマップを作成 (高速化)
        addr_map = {item["address"]: i for i, item in enumerate(lst)}
        dangling = {}
        for target_addr, label_str in labels.items():
            if target_addr in addr_map:
                lst[addr_map[target_addr]].update(label=label_str)
            else:
                dangling[target_addr] = label_str.removesuffix(":")
        return dangling

    def _tmpl(self, asm):
        """アセンブルリストからアセンブル文字列生成

        Args:
            asm (list): ニーモニックとオペランドのトークンリスト。

        Returns:
            str: アセンブリ文字列。
        """
        mnemonic = asm[0].upper()
        if len(asm) == 1:
            return mnemonic

        # カンマの隣にスペースを挿入して可読性を上げる
        operands_str = "".join(asm[1:]).replace(",", ", ")
        return f"{mnemonic} {operands_str}"

    def _reladdr(self, x, y):
        """相対アドレス計算

        Args:
            x (int): 相対オフセットバイト値。
            y (int): 現在のアドレス（ジャンプ命令のアドレス）。

        Returns:
            int: 算出した絶対ジャンプ先アドレス。
        """
        maxword = 0xFFFF
        return (((x - 0x100) if (x & 0x80) else (x & 0x7F)) + y + 2) & maxword

    def _handle_1byte(self, u, opcode, adr):
        """1バイト命令のアセンブリ文字列を生成します。

        Args:
            u (dict): オペコード情報辞書。
            opcode (list): オペコードバイト列。
            adr (int): 現在のアドレス。

        Returns:
            str: アセンブリ文字列。
        """
        return self._tmpl(u["asm"])

    def _handle_2bytes(self, u, opcode, adr):
        """2バイト命令のアセンブリ文字列を生成します。

        Args:
            u (dict): オペコード情報辞書。
            opcode (list): オペコードバイト列。
            adr (int): 現在のアドレス。

        Returns:
            str: アセンブリ文字列。
        """
        tmpl = self._tmpl(u["asm"])
        if u.get("rel") is not None:
            # ラベル文字列を先に組み立てて差し込む（format は掛けない）。
            # 名前に `{` が含まれても壊れないようにするため。
            return tmpl.replace(
                "0x{0}", self._label_text(self._reladdr(opcode[1], adr))
            )
        return tmpl.replace("{0}", "{0:02X}").format(opcode[1])

    def _handle_3bytes(self, u, opcode, adr):
        """3バイト命令のアセンブリ文字列を生成します。

        Args:
            u (dict): オペコード情報辞書。
            opcode (list): オペコードバイト列。
            adr (int): 現在のアドレス。

        Returns:
            str or None: アセンブリ文字列。対応する命令がない場合はNone。
        """
        if u.get("jmp") is not None:
            target = (opcode[2] << 8) | opcode[1]
            return self._tmpl(u["asm"]).replace("0x{1}{0}", self._label_text(target))

        op_type = u.get("type")
        if op_type == "byte":
            return self._tmpl(u["asm"]).replace("{0}", "{0:02X}").format(opcode[2])

        if op_type == "word":
            return self._word_operand(
                self._tmpl(u["asm"]),
                opcode[1],
                opcode[2],
                self._word_mode(u["asm"]),
                site=adr,
            )
        return None

    def _handle_4bytes(self, u, opcode, adr):
        """4バイト命令のアセンブリ文字列を生成します。

        Args:
            u (dict): オペコード情報辞書。
            opcode (list): オペコードバイト列。
            adr (int): 現在のアドレス。

        Returns:
            str or None: アセンブリ文字列。対応する命令がない場合はNone。
        """
        if u.get("ext") is not None:
            # ddcb / fdcb
            return self._tmpl(u["asm"]).replace("{0}", "{0:02X}").format(opcode[2])

        op_type = u.get("type")
        if op_type == "word":
            return self._word_operand(
                self._tmpl(u["asm"]),
                opcode[2],
                opcode[3],
                self._word_mode(u["asm"]),
                site=adr,
            )
        if op_type == "byte":
            # `ld (ix+d), n` の d と n。アドレスではないので置き換え対象外。
            return (
                self._tmpl(u["asm"])
                .replace("{0}", "{0:02X}")
                .replace("{1}", "{1:02X}")
                .format(opcode[2], opcode[3])
            )
        return None


def disassemble(
    data: bytes,
    start_address: int = 0,
    data_regions: list[list[int]] | None = None,
    m1_handler=None,
    label_addresses: list[int] | None = None,
    strmap: tuple | None = None,
    label_names: dict | None = None,
    equ_names: dict | None = None,
    valid_ranges: list[list[int]] | None = None,
    raw_operand: list[int] | None = None,
    comments: dict | None = None,
) -> list[str]:
    """Z80バイナリデータを逆アセンブルしてアセンブリコードのリストを返します。

    Args:
        data (bytes): 逆アセンブル対象のバイナリデータ
        start_address (int, optional): 開始アドレス. Defaults to 0.
        data_regions (list | None, optional): データ領域のリスト。
            各要素は [開始アドレス, 終了アドレス]（両端含む）。
            指定範囲は命令として解釈されず db として出力される。
            `{"range": [開始, 終了], "fmt": "b w b", "per_line": 8}` と辞書で書くと、
            `fmt` の型の並びを範囲の先頭から繰り返します（`w` は 2 バイトを `dw`
            1 行にまとめ、値に名前が付いていれば置き換える）。`per_line` は `db` を
            1 行にまとめるバイト数です。`fmt` で範囲を割り切れないときの警告は
            この関数では受け取れません。要るなら `Disasm` を使い `warnings` を
            読んでください。Defaults to None.
        m1_handler (callable | None, optional): M1サイクル復号ハンドラー。
            (address: int, byte: int) -> int の形式。Defaults to None.
        label_addresses (list[int | str] | None, optional): 強制的にラベルを
            付与するアドレスのリスト。コード中に参照のないエントリポイント（NMI 等）に
            `L_xxxx:` ラベルを付けるのに使う。整数アドレスのほか、シンボル名
            (RESET/RST0-7/IM1/NMI) や数値文字列 ("0x0066") も指定できる。
            walk() の extra_entries と同じリストを渡すと一貫したラベル付けになる。
            Defaults to None.
        strmap (tuple | None, optional): キャラクターコード表（256要素のタプル）。
            データ領域の `db 0xXX ; [文字]` コメントに使う文字を決める。
            未指定時は標準ASCIIテーブル（0x20〜0x7E）を使用。Defaults to None.
        label_names (dict | None, optional): `{アドレス: 名前}`。指定した
            アドレスのラベルが `L_0066@NMI` の形になり、定義側にも参照側にも
            同じ綴りで出ます。**住所は名前に残ります**（`walk` と `_auto_entry` が
            ラベル名から住所を読み戻しているため）。名前を付けたアドレスには
            参照が無くてもラベルが付き、そこを指す 16 ビットオペランド
            （`LD de, nn` など分岐以外の 22 命令）もラベルに変わります。
            `label_addresses` はこの置き換えの対象になりません。
            値に `{"name": 名前, "imm": False}` と書くと、**そのアドレスは 16 ビット
            即値の置き換えから外れます**（間接参照と分岐は名前のまま）。RST ベクタ
            のような小さいアドレスに名前を付けるときに要ります。`"comment"` を書くと
            ラベル行の前に説明が出ます。
            **逆アセンブル範囲外のアドレスは指定できません**（定義行を置く場所が
            無いため）。RAM / I/O は `equ_names` を使ってください。Defaults to None.
        equ_names (dict | None, optional): `{アドレス: 名前}` または
            `{アドレス: {"r": 読み名, "w": 書き名}}`。逆アセンブル範囲外の定数
            （RAM・I/O・ハードウェアレジスタ）に名前を付けます。出力の先頭に
            `MirrorRam: EQU 0x8000` を置き、参照側は**裸の名前**になります
            （`L_xxxx@` は付きません）。read と write で役割が違うレジスタは
            命令の形から向きを判定して名前を選びます。`"name"` は書いていない
            向きすべての名前、`"comment"` は `EQU` 行に付ける説明です。
            Defaults to None.
        valid_ranges (list[list[int]] | None, optional): バイナリが実在する
            アドレス範囲 `[[開始, 終了], ...]`（両端含む）。`bins` で複数ファイルを
            別々のアドレスに置いたときの**隙間を出力から除外**します。未指定なら
            全域を走査します（隙間の `0x00` が `nop` として出ます）。
            `walk()` の同名引数と同じものを渡してください。Defaults to None.
        raw_operand (list[int] | None, optional): 16 ビットオペランドを数値のまま
            出す**命令のアドレス**。`label_names` と `equ_names` の両方に効きます。
            同じ値が、ある場所ではアドレス・別の場所では定数、という混在を 1 か所ずつ
            潰すためのものです。`labels` の `imm` はアドレスごと、こちらは命令ごとの
            指定になります。Defaults to None.
        comments (dict | None, optional): `{アドレス: 文字列}` または
            `{アドレス: {"line": 行末, "block": 行の前}}`。出力へコメントを出します。
            範囲外や命令の途中を指すと `ValueError` になります。Defaults to None.

    Returns:
        list[str]: アセンブリコードの各行のリスト
    """
    d = Disasm()
    if data_regions:
        d.datamap = data_regions
    if valid_ranges:
        d.valid_ranges = valid_ranges
    if m1_handler:
        d.m1_handler = m1_handler
    if label_addresses:
        d.label_addresses = [parse_entry(a) for a in label_addresses]
    if strmap:
        d.cpu.strmap = strmap
    if label_names:
        # キーの解決と名前の検証はセッターが行う
        d.label_names = label_names
    if equ_names:
        d.equ_names = equ_names
    if raw_operand:
        d.raw_operand = raw_operand
    if comments:
        d.comments = comments
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
