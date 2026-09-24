#!/usr/bin/env python3


from pz80 import _directives, _encoder, _preprocess, _tokenizer, z80
from pz80._util import format_location as _format_location


class Asm:
    """Z80アセンブラクラス"""

    def __init__(self):
        """Asmクラスを初期化します。"""
        self.cpu = z80.Z80()
        self.directive_handler = _directives.DirectiveHandler(self)
        self.encoder = _encoder.Encoder(self.cpu)
        self.preprocessor = _preprocess.Preprocessor(self.cpu)

        # バイトを出力する疑似命令のディスパッチ表。別名（defb 等）を含む。
        # Pass 1 はサイズ確定、Pass 2 はラベル解決後の値埋めを担う。
        h = self.directive_handler
        self._directive_pass1 = {
            "ds": h.process_ds_pass1,
            "defs": h.process_ds_pass1,
            "align": h.process_align_pass1,
            "db": h.process_db_pass1,
            "defb": h.process_db_pass1,
            "dw": h.process_dw_pass1,
            "defw": h.process_dw_pass1,
        }
        self._directive_pass2 = {
            "db": h.process_db_pass2,
            "defb": h.process_db_pass2,
            "dw": h.process_dw_pass2,
            "defw": h.process_dw_pass2,
        }

    def assemble_lines(self, lines, file=None):
        """行リストからアセンブルを実行します。

        Args:
            lines (list): ソースコードの行リスト (例: ["org 0x100\\n", "ld a, 0x10\\n"])
            file (str, optional): エラーメッセージで使用するソース識別子 (ファイル名等)。

        Returns:
            list: アセンブル済みリスト。ソース上の並び順に次の3種類が入る。
                命令行: {"line": int, "file": str|None, "asm": list, "base": int, "offset": int, "opcode": list}
                ラベル行: {"line": int, "file": str|None, "label": str, "base": int, "offset": int}
                消費行: {"line": int, "file": str|None, "asm": list, "kind": str}
                    kind は "equ"/"org"/"if"/"elseif"/"else"/"endif"/"skipped" のいずれか。
                    番地を占有しないため "opcode"/"base"/"offset" を持たない。
        """
        return self.assemble_chunks([(file, lines)])

    def assemble_chunks(self, chunks):
        """複数のソースチャンクを連結してアセンブルを実行します。

        **チャンクはリストの順に上から連結されます。** 独立した翻訳単位ではなく、
        1 本のソースとして扱われるため、次の状態がチャンクを跨いで継続します。

        * `ORG` で設定したベースアドレス
        * ラベル・`EQU` 定数（後のチャンクから前のチャンクの定義を参照できる）
        * `IF` / `ELSE` / `ENDIF` のネスト状態（IF がチャンクを跨いでもよい）

        `INCLUDE` 疑似命令の代替として設計しているため、この「テキストを貼り付ける」
        意味づけが正となります。

        行番号は**チャンクごとに 1 から**振られます。したがってエラー位置の特定には
        識別子が必要です。識別子が重複した場合は 2 つ目以降に `#2`, `#3` … を付けて
        一意にします（同じファイルを 2 回取り込む使い方を妨げないため）。

        Args:
            chunks (list[tuple[str|None, list[str]]]): (ソース識別子, 行リスト) のリスト。
                ソース識別子はエラーメッセージで使用される (ファイル名でも任意の文字列でも可)。
                `None` と空文字列は「識別子なし」として扱われ、
                エラーメッセージが `line 5` の形になる。
                行リストはタプル等の iterable でもよいが、1 本の文字列は不可
                （1 文字ずつイテレートされてしまうため）。
                例: [("header.asm", ["..."]), ("main.asm", ["..."])]

        Returns:
            list: アセンブル済みリスト。assemble_lines() と同形式。
                `EQU` / `ORG` / `IF` / `ELSE` / `ENDIF` と条件が偽で捨てられた行も
                `kind` を持つ行として元の位置に含まれる（渡したソースが実際に
                どう解釈されたかを確認できるようにするため）。

        Raises:
            ValueError: 識別子が文字列でも None でもない場合、
                行リストが 1 本の文字列の場合、行リストが iterable でない場合。
        """
        # 各チャンクをトークン化してファイル名情報を付与
        src = []
        for file, lines in self._resolve_chunk_names(chunks):
            src.extend(_tokenizer.source(lines, file=file))

        # ラベル抽出・ORG処理・条件アセンブル・構造化・EQU置換
        asm = self.preprocessor.run(src)

        # Pass 1: オペコード生成・アドレス仮決定
        self._pass1(asm)

        # Pass 2: ラベルアドレスを確定・オペコードに反映
        self._pass2(asm)

        return self._merge_consumed(asm, self.preprocessor.consumed)

    @staticmethod
    def _merge_consumed(asm, consumed):
        """出力行を持たない行を元の並び順どおりに戻します。

        `EQU` / `ORG` / `IF` / `ELSE` / `ENDIF` と条件が偽で捨てられた行は
        バイトを生成しないため Pass 1/2 には渡しません。ただし結果から丸ごと
        消えていると、利用者から見て「疑似命令として消費された」のか
        「条件アセンブルで落とされた」のかが区別できません。`kind` を持つ行として
        戻すことで、渡したソースが実際にどう解釈されたかを確認できます。

        Args:
            asm (list): Pass 2 まで終えた構造化リスト。
            consumed (list): `(挿入位置, 行)` のリスト。位置は昇順。

        Returns:
            list: 消費された行を挿入位置に戻したリスト。
        """
        if not consumed:
            return asm

        out = []
        i = 0
        for pos, row in consumed:
            # END 疑似命令で asm が切り詰められている場合は末尾までで止める
            pos = min(pos, len(asm))
            out.extend(asm[i:pos])
            out.append(row)
            i = pos
        out.extend(asm[i:])
        return out

    @staticmethod
    def _resolve_chunk_names(chunks):
        """チャンクの型を検証し、識別子の重複に連番を付けて一意にします。

        識別子はエラー位置の特定に使いますが、行番号がチャンクごとに 1 から
        振り直されるため、識別子が重複すると位置を特定できなくなります。
        かといって重複を拒否すると、同じファイルを 2 回取り込む正当な使い方を
        妨げてしまいます。そこで 2 つ目以降に `#2`, `#3` … を付けて区別します。

        `None` と空文字列は「識別子なし」として区別せず、そのまま通します
        （複数あるとエラー位置を特定できないため、識別子を付けることを推奨）。

        行リストは中身までは検査せず、1 本の文字列でないことと iterable で
        あることだけを見ます。文字列を渡すと 1 文字ずつイテレートされ、
        本来と無関係な "Unknown mnemonic 'P'" のようなエラーになるためです。

        チャンク自体の形（2 要素の組であること）も先に見ます。`assemble()` が
        ソース文字列とチャンクリストの両方を受けるようになったため、
        行リストを直接渡す間違いが起こりやすいためです。

        Args:
            chunks (list[tuple[str|None, list[str]]]): 元のチャンクリスト。

        Returns:
            list[tuple[str|None, list[str]]]: 識別子を一意化したチャンクリスト。

        Raises:
            ValueError: チャンクが 2 要素の組でない場合、識別子が文字列でも
                None でもない場合、行リストが 1 本の文字列の場合、
                行リストが iterable でない場合。
        """
        seen = {}
        resolved = []
        for chunk in chunks:
            # ソース文字列や行リストを直接渡した場合、アンパックの
            # TypeError になって原因が読み取れないため先に形を見る。
            if isinstance(chunk, str) or len(tuple(chunk)) != 2:
                raise ValueError(
                    f"Each chunk must be a (identifier, lines) pair: {chunk!r}"
                )
            name, lines = chunk

            if name is not None and not isinstance(name, str):
                raise ValueError(f"Chunk identifier must be a string or None: {name!r}")

            # 1 本の文字列は 1 文字ずつイテレートされ、無関係な位置で
            # "Unknown mnemonic 'P'" 等になって原因が読み取れないため弾く。
            if isinstance(lines, str):
                raise ValueError(
                    f"Chunk lines must be a list of lines, not a single string "
                    f"(chunk {name!r}); use splitlines() to split it into lines"
                )
            try:
                iter(lines)
            except TypeError:
                raise ValueError(
                    f"Chunk lines must be an iterable of lines "
                    f"(chunk {name!r}): {lines!r}"
                ) from None

            if not name:  # None と "" は識別子なしとして扱う
                resolved.append((None, lines))
                continue

            count = seen.get(name, 0) + 1
            seen[name] = count
            resolved.append((name if count == 1 else f"{name}#{count}", lines))

        return resolved

    def exec(self, name, defines=None):
        """アセンブル処理メイン (ファイル入力)

        Args:
            name (str): ソースファイル名
            defines (dict | None): 外部から与えるシンボル定義 `{名前: 値}`。
                ソース先頭に `名前: EQU 値` を前置したのと同じ扱いになるため、
                条件アセンブル (`IF`) から参照できる。CLI の `-D` に対応する。

        Returns:
            list: assemble_lines() と同形式のアセンブル済みリスト。
        """
        try:
            with open(name, encoding="utf-8") as f:
                fs = f.readlines()

        except FileNotFoundError as e:
            raise FileNotFoundError(f"Source file not found: {name}") from e

        if not defines:
            return self.assemble_lines(fs, file=name)

        # 合成した EQU 行として前置する。既存の EQU 機構（大文字正規化・
        # 範囲検証・シンボル置換）をそのまま再利用できる。
        equ_lines = [f"{key}: equ {value}\n" for key, value in defines.items()]
        return self.assemble_chunks([("-D", equ_lines), (name, fs)])

    @property
    def symbols(self):
        """シンボル表（`SymbolTable`）。

        実体は前処理層が持つ。`Asm` 側で複製すると、前処理を個別に呼んだ場合に
        両者が食い違うため参照で返す。
        """
        return self.preprocessor.symbols

    @property
    def labelmap(self):
        """アセンブラソースから抽出したラベル・EQU定数のリスト。

        `SymbolTable` へのビュー。`value` はラベルの場合 Pass 1 で確定する。
        """
        return self.symbols.as_labelmap()

    @property
    def label2address(self):
        """ラベルと確定アドレスの対応リスト。

        `SymbolTable` へのビュー。Pass 1 が `set_address()` で埋める。
        """
        return self.symbols.as_label2address()

    def _pass1(self, asm):
        """アセンブル処理 その1: オペコード生成とアドレス仮決定

        各命令のオペコードを生成し base/offset を確定する。ラベルのアドレスも仮決定する。
        処理後、self.label2address にラベルと確定アドレスの対応が格納される。

        Args:
            asm (list): _pass0() が生成したアセンブルリスト。
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

        Note:
            処理後、命令行に "opcode" および "fixups" キーが追加される。
                "opcode": [int, ...]  生成されたオペコードバイト列
                "fixups": [{"offset": int, "size": int, "type": str, "src": dict}, ...]
                          Pass 2でアドレス解決が必要なバイト位置の情報
        """
        current_offset = 0
        last_base = None

        # 未定義シンボルの判定に使う。表は pass0 の時点で全シンボルを
        # 持っているので、Pass 1 用にセットを作り直す必要はない。
        self.encoder.defined_labels = self.symbols

        for item in asm:
            # ベースアドレスが変わった場合 (ORGなど)
            if item["base"] != last_base:
                current_offset = 0
                last_base = item["base"]

            # asm行
            if item.get("asm") is not None:
                # $ が正しく解決できるよう全ディレクティブ/命令の処理前に base/offset を確定
                item["base"] = last_base
                item["offset"] = current_offset

                # END 疑似命令: 以降の行を無視してアセンブル終了
                if item["asm"][0].lower() == "end":
                    del asm[asm.index(item) :]
                    break

                current_offset += self._encode_line(item)

            # label行
            elif item.get("label") is not None:
                # アドレス確定してシンボル表へ書き戻す
                self.symbols.set_address(item["label"], last_base + current_offset)
                # ラベルのオフセット更新
                item.update({"offset": current_offset})

    def _encode_line(self, item):
        """1 行を符号化して `item` に opcode を設定し、消費バイト数を返します。

        バイトを出力する疑似命令（DS / DB / DW とその別名）はディスパッチ表から
        ハンドラを引き、それ以外は通常の命令として符号化します。

        Args:
            item (dict): 対象のアセンブル行。`opcode`（と命令なら `fixups`）が設定される。

        Returns:
            int: 生成したバイト数。

        Raises:
            ValueError: オペランドの組み合わせが不正な場合。
        """
        handler = self._directive_pass1.get(item["asm"][0].lower())
        if handler is not None:
            opcodes = handler(item)
            item.update({"opcode": opcodes})
            return len(opcodes)

        op, fixups = self.encoder.asm2op(item)
        if not op:
            raise ValueError(
                f"Invalid operand combination for '{item['asm'][0]}' on "
                f"{_format_location(item['line'], item.get('file'))}: "
                f"{' '.join(item['asm'])}"
            )
        item.update({"opcode": op, "fixups": fixups})
        return len(op)

    def _pass2(self, asm):
        """アセンブル処理 その2: ラベルアドレスを確定してオペコードに反映

        Args:
            asm (list): _pass1() 処理済みのアセンブルリスト。
                        命令行には "opcode" と "fixups" キーが含まれる。
                        例: [{"line": 2, "asm": ["jp", "LABEL"], "base": 0x100, "offset": 2,
                              "opcode": [0xC3, 0x00, 0x00],
                              "fixups": [{"offset": 1, "size": 2, "type": "word", "src": {...}}]}, ...]
        """
        # ラベル名 -> 確定アドレスの辞書。Pass 1 が set_address() で埋めた
        # 表から作る（以前は label2address のリストから毎回組み直していた）。
        address_map = self.symbols.addresses()

        for p in asm:
            if "asm" not in p:
                continue

            handler = self._directive_pass2.get(p["asm"][0].lower())
            if handler is not None:
                handler(p, address_map)
            else:
                self._pass2_instruction(p, address_map)

    def _pass2_instruction(self, p, address_map):
        """通常命令のアドレス解決

        fixups リストを参照し、opcode 内の仮値バイトを確定アドレスで上書きする。

        Args:
            p (dict): _pass1() 処理済みの命令行辞書。
                      各要素:
                        "line"(int):
                        "asm"([str, ...]):
                        "base"(int):
                        "offset"(int):
                        "opcode"([int, ...])
                        "fixups"([{"offset": int, "size": int, "type": str, "src": dict}, ...]):

                      例: {"line": 5, "asm": ["jp", "LABEL"], "base": 0x100, "offset": 2,
                           "opcode": [0xC3, 0x00, 0x00],
                           "fixups": [{"offset": 1, "size": 2, "type": "word",
                           "src": {"value": 0, "location": 1, "length": 1}}]}
            address_map (dict): ラベルとアドレスの対応辞書 (例: {"LABEL": 0x100})
        """
        fixups = p.get("fixups", [])
        if not fixups:
            return

        opcode = p["opcode"]

        current_address = p["base"] + p["offset"]
        for fixup in fixups:
            src = fixup["src"]
            # Pass1で特定した位置から式を再評価
            address, consumed = self.encoder.evaluate_expression(
                p["asm"],
                src["location"],
                address_map,
                p["line"],
                current_address=current_address,
            )

            if address is None:
                # 式が解決できない場合はスキップ（あるいはエラー）
                continue

            if fixup["type"] == "rel":
                # 相対ジャンプだけは「今の PC からの差」を求める必要があり、
                # 絶対値を書く byte / word とは計算が別物なので分けて扱う。
                self._apply_rel_fixup(p, opcode, fixup, address)
            else:
                self._apply_value_fixup(p, opcode, fixup, address)

    # fixup 種別ごとの「許容範囲・書き込むバイト数・メッセージ中の語」。
    # byte と word は範囲と幅が違うだけで書き込み方は同じなので表にまとめる。
    _VALUE_FIXUPS = {
        "byte": (-128, 255, 1, "Byte"),
        "word": (-32768, 65535, 2, "Word"),
    }

    def _apply_value_fixup(self, p, opcode, fixup, address):
        """絶対値の fixup（byte / word）を opcode へ書き込みます。

        範囲検査は `Encoder.check_range` に委ねます。Pass 1 の即値検査と
        **同じメッセージ形式**にするためで、以前はここだけ
        `Byte value out of range: 256 on line 7` という別形式を出していました。

        Args:
            p (dict): 対象のアセンブル行（位置情報に使う）。
            opcode (list): 書き換え対象のオペコードバイト列。
            fixup (dict): `_pass1` が残した修正情報。
            address (int): 解決済みの値。

        Raises:
            ValueError: 値が種別の許容範囲を外れる場合。
        """
        low, high, size, kind = self._VALUE_FIXUPS[fixup["type"]]
        self.encoder.check_range(address, low, high, kind, p)

        # リトルエンディアンで size バイト書く（byte は 1 バイトなので下位のみ）。
        for i in range(size):
            opcode[fixup["offset"] + i] = (address >> (8 * i)) & 0xFF

    @staticmethod
    def _apply_rel_fixup(p, opcode, fixup, address):
        """相対ジャンプの fixup を opcode へ書き込みます。

        変位は**次の命令の先頭**（現在アドレス + 命令長）からの差です。
        Z80 は 8bit 符号付きなので -128〜+127 に収まる必要があります。

        Args:
            p (dict): 対象のアセンブル行。
            opcode (list): 書き換え対象のオペコードバイト列。
            fixup (dict): `_pass1` が残した修正情報。
            address (int): 解決済みのジャンプ先アドレス。

        Raises:
            ValueError: 変位が 8bit 符号付きの範囲を外れる場合。
        """
        pc = p["base"] + p["offset"] + len(opcode)
        offset = address - pc
        if not (-128 <= offset <= 127):
            loc = _format_location(p["line"], p.get("file"))
            raise ValueError(f"Relative jump out of range ({offset}) on {loc}")

        opcode[fixup["offset"]] = offset & 0xFF


def assemble(source):
    """Z80ソースコードをアセンブルしてバイナリデータを返します。

    ソース文字列と、`assemble_chunks()` と同じチャンクリストの両方を受け付けます。

        assemble("org 0x100\nld a, 1\n")
        assemble([("header.asm", [...]), ("main.asm", [...])])

    `Asm().assemble_chunks()` の戻り値は**行の並び**であってメモリイメージでは
    ないため、`opcode` を単純に連結すると `ORG` で飛ばした範囲が詰まります。
    バイト列が欲しいだけならこの関数を使ってください。

    Args:
        source (str | list): アセンブリソースコード、または
            `[(識別子, 行リスト), ...]` のチャンクリスト。

    Returns:
        bytes: アセンブルされたバイナリデータ。配置先の最小アドレスから
            最大アドレスまでを返し、隙間は `0x00` で埋めます。

    Raises:
        ValueError: チャンクの形式が不正な場合。
    """
    assembler = Asm()
    if isinstance(source, str):
        result = assembler.assemble_lines(source.splitlines())
    else:
        result = assembler.assemble_chunks(source)
    return to_bytes(result)


def to_bytes(result):
    """アセンブル済みリストをバイト列へ変換します。

    戻り値は行の並びなので、`base + offset` をキーにメモリイメージを組み直します。
    `ORG` で飛ばした範囲は行として存在しないため、`opcode` の単純連結では
    隙間が詰まってしまいます。

    Args:
        result (list): `assemble_lines()` / `assemble_chunks()` の戻り値。

    Returns:
        bytes: 配置先の最小アドレスから最大アドレスまでのバイト列。
            隙間は `0x00`。出力バイトが 1 つも無ければ空の bytes。
    """
    memory = {}
    for item in result:
        addr = item.get("base", 0) + item.get("offset", 0)
        for i, byte in enumerate(item.get("opcode") or []):
            memory[addr + i] = byte

    if not memory:
        return b""

    lo, hi = min(memory), max(memory)
    data = bytearray(hi - lo + 1)
    for addr, byte in memory.items():
        data[addr - lo] = byte
    return bytes(data)
