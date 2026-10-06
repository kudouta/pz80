#!/usr/bin/env python3

"""walk 用エントリポイントの自動抽出（ディスパッチ定型句カタログ方式）。

`JP (hl)` のような間接分岐で CFG トレースは必ず停止する。分岐先はジャンプ
テーブル内の値であり、静的には辿れない。本モジュールは**手書きアセンブラの
ディスパッチは書き方の定型句が有限個しかない**という前提に立ち、到達済み
コードからその定型句を認識してテーブル基底を逆算する。

ROM 全域からアドレステーブルらしき語の並びを総当たり探索する方式は採らない。
「ROM 内を指す」「命令境界を指す」といったテーブル側の性質は必要条件にすぎず、
スプライト定義や面データが偶然これを満たすため候補が数百件出て実用にならない。
テーブル側の性質は検証にのみ使い、候補生成はディスパッチャ側の定型句から行う。

`pz80 walk --auto-entry` から利用する。
"""

import re

from pz80._vectors import VECTOR_ALIASES
from pz80.walk import build_addr_map, classify_instruction, sweep_from, trace

# 逆アセンブラ出力に対するパターン
# 16 ビットアドレスの表記。`0x0120` と、disasm が label_names で名前を添えた
# `L_1240@MSG_TABLE` の両方を受ける。捕捉群は 1 つだけなので、どちらの綴りでも
# 同じ group 番号で 4 桁が取れる（呼び出し側の group 番号を変えずに済む）。
# 名前部分は `[\w.]+`。`.` は pz80 のラベルとして合法で、`StrA.D.1980` のような
# 名前が実際に書かれる（`\w` だけだとドットで切れて住所の取りこぼしが起きる）。
ADDR16 = r"(?:0x|L_)([0-9A-Fa-f]{4})(?:@[\w.]+)?"

RE_LD_RR_IMM = re.compile(rf"^LD\s+(hl|ix|iy|de|bc),\s*{ADDR16}$", re.I)
RE_LD_HL_IND = re.compile(rf"^LD\s+hl,\s*\({ADDR16}\)$", re.I)
RE_ST_HL_IND = re.compile(rf"^LD\s+\({ADDR16}\),\s*hl$", re.I)
RE_LD_A_IMM = re.compile(r"^LD\s+a,\s*0x([0-9A-Fa-f]{2})$", re.I)
RE_ST_A_IND = re.compile(rf"^LD\s+\({ADDR16}\),\s*a$", re.I)
RE_JP_IND = re.compile(r"^JP\s+\((hl|ix|iy)\)$", re.I)
RE_POP_PTR = re.compile(r"^POP\s+(hl|ix|iy)$", re.I)
RE_RST = re.compile(r"^RST\s+(0x[0-9A-Fa-f]+|\d+)$", re.I)
RE_LD_HL_IMM = re.compile(r"^LD\s+hl,\s*0x([0-9A-Fa-f]{4})$", re.I)
# `@名前` は省略可。disasm が label_names でラベルに名前を添えた出力も読めるようにする。
RE_CALL = re.compile(r"^CALL\s+(?:(\w+),\s*)?L_([0-9A-Fa-f]{4})(?:@[\w.]+)?$", re.I)

# 後方スライスの上限（命令数）。`_bases_near()` がテーブル基底の候補を拾うために
# 分岐地点から遡る距離で、実際は**基本ブロックの境界（無条件 RET / JP）で先に
# 止まる**（`_backward()` 参照）。つまりこれは暴走を防ぐ頭打ちでしかない。
#
# **24 に測定上の根拠は無い。** 手元の ROM 3 本で窓幅を 1 まで狭めても、最終的な
# エントリ集合は変わらなかった。テーブル基底を積む `LD hl,nnnn` が分岐の直前に
# 来るためである。広げても候補が増えるだけで、絞り込みは後段（`in_rom` 判定と
# `MIN_TABLE`）が行うので、余裕を大きく取ってある。
BACK_WINDOW = 24
# テーブルとして採用する最小要素数
MIN_TABLE = 2
# コードらしさ判定で追う命令数
PLAUSIBLE_DEPTH = 8
# RST のベクタ先を「テーブル分岐か」判定するときに追う命令数。実物は
# `ADD a,a / POP hl / LD e,a / LD d,0 / ADD hl,de / LD e,(hl) / INC hl /
# LD d,(hl) / EX de,hl / JP (hl)` の 10 命令なので、少し余裕を持たせてある。
RST_BODY_WINDOW = 16
# `push-return` で「積んだ値が積まれたままか」を見る命令数
PUSH_RETURN_WINDOW = 8


class Finding:
    """1 件の抽出結果。

    Attributes:
        pattern (str): 認識した定型句の識別子。
        site (int): 定型句を検出したアドレス。
        base (int | None): ジャンプテーブルの基底アドレス。解決不能なら None。
        stride (int): テーブルの 1 要素のバイト数 (2=dw, 3=JP 命令列)。
        targets (list[int]): 抽出したエントリポイント。
        note (str): 補足（解決不能な場合の理由など）。
    """

    def __init__(self, pattern, site, base=None, stride=2, targets=None, note=""):
        """Finding を初期化します。

        Args:
            pattern (str): 認識した定型句の識別子（`jp-indirect` など）。
            site (int): 定型句を検出したアドレス。
            base (int | None): ジャンプテーブルの基底アドレス。テーブルが無い
                定型句（`rst-vector` / `sp-ret` など）では None。
            stride (int): テーブルの 1 要素のバイト数 (2=dw, 3=JP 命令列)。
            targets (list[int] | None): 抽出したエントリポイント。None なら空。
            note (str): 補足（解決不能な場合の理由など）。
        """
        self.pattern = pattern
        self.site = site
        self.base = base
        self.stride = stride
        self.targets = targets or []
        self.note = note

    def format(self):
        """1 行の説明文を返す。

        テーブルが無い定型句（`rst-vector` / `sp-ret`）でも、分岐先が分かって
        いれば `-> ...` を出します。以前は `note` だけを見ていたため
        `rst-vector` の行が `@0x0006` で終わり、ベクタアドレスが読めませんでした。

        Returns:
            str: `[jp-indirect] @0x0120 table=0x0140 stride=2 -> 0x0200 0x0210`
                のような 1 行。テーブルが無ければ `table=` / `stride=` を省きます。
        """
        if self.base is None:
            targets = " ".join(f"0x{t:04X}" for t in self.targets)
            tail = " ".join(
                x for x in (f"-> {targets}" if targets else "", self.note) if x
            )
            return f"[{self.pattern}] @0x{self.site:04X} {tail}"
        targets = " ".join(f"0x{t:04X}" for t in self.targets)
        return (
            f"[{self.pattern}] @0x{self.site:04X} table=0x{self.base:04X} "
            f"stride={self.stride} -> {targets}"
        )

    def __repr__(self):
        """デバッグ表示用の文字列を返します。

        Returns:
            str: `<Finding [rst-vector] @0x0102 -> 0x0028>` の形。
        """
        return f"<Finding {self.format()}>"


class AutoEntry:
    """ディスパッチ定型句からエントリポイントを反復抽出する。

    Args:
        data (bytes | list[int]): ROM イメージ。
        start (int): ロードアドレス。valid_ranges 指定時は 0 基点として扱う。
        m1_handler (callable | None): M1サイクル復号ハンドラー。
        valid_ranges (list[list[int]] | None): 有効なアドレス範囲 [[start, end], ...]。
    """

    def __init__(self, data, start=0, m1_handler=None, valid_ranges=None):
        """AutoEntry を初期化し、ROM 全体の命令マップを作ります。"""
        self.data = list(data)
        self.size = len(self.data)
        self.start = 0 if valid_ranges is not None else start
        self.valid_ranges = valid_ranges

        self.m1_handler = m1_handler
        self.amap = build_addr_map(self.data, start, m1_handler, valid_ranges)
        self.order = sorted(self.amap)
        self.index = {a: i for i, a in enumerate(self.order)}

        # 直後へ戻らないと分かった命令のアドレス（RST 直後にテーブルを埋め込む形）。
        # `trace()` に渡して、テーブルのバイトをコードにしないために使う。
        # `run()` のあと呼び出し側が読む（`__main__` が `walk()` へ渡す）。
        self.no_fallthrough = set()

    # ------------------------------------------------------------------ 基本部品

    def in_rom(self, addr):
        """アドレスが ROM 範囲（かつ valid_ranges 内）かを返す。

        Args:
            addr (int): 調べるアドレス。

        Returns:
            bool: ROM のバイトがあるアドレスなら True。
        """
        if not (self.start <= addr < self.start + self.size):
            return False
        if self.valid_ranges is None:
            return True
        return any(r[0] <= addr <= r[1] for r in self.valid_ranges)

    def byte(self, addr):
        """アドレスの生バイトを返す。範囲外なら None。

        復号はしません。`m1_handler` があっても、ROM に書かれたままの値です。

        Args:
            addr (int): 読むアドレス。

        Returns:
            int | None: 0〜255 のバイト値。ROM の外なら None。
        """
        if not self.in_rom(addr):
            return None
        return self.data[addr - self.start]

    def resweep(self, addr):
        """`amap` に無い分岐先から走査し直して命令マップを返す。

        先頭からの走査が作った命令境界に分岐先が乗らないことがあります
        （詳細は `walk.sweep_from()`）。これを渡さないと追跡が静かに止まり、
        到達できるコードをデータと誤判定します。

        Args:
            addr (int): 走査を始めるアドレス。

        Returns:
            dict[int, dict]: アドレスをキーとする逆アセンブル結果。
        """
        if not self.in_rom(addr):
            return {}
        found = sweep_from(self.data, addr, self.start, self.m1_handler)
        return {a: p for a, p in found.items() if self.in_rom(a)}

    def trace(self, entries):
        """CFG トレースし (到達バイト集合, 命令先頭集合) を返す。

        `resweep` で見つかった命令は `self.amap` に加わるため、
        アドレス順の索引を作り直します。

        Args:
            entries (iterable[int]): 追跡を始めるエントリポイント。

        Returns:
            tuple[set[int], set[int]]: (到達したバイトの集合, 命令先頭アドレスの集合)。
                `walk.trace()` の戻り値そのもの。
        """
        before = len(self.amap)
        result = trace(
            self.amap, entries, resweep=self.resweep, stop_after=self.no_fallthrough
        )
        if len(self.amap) != before:
            self.order = sorted(self.amap)
            self.index = {a: i for i, a in enumerate(self.order)}
        return result

    def _ensure_mapped(self, addr):
        """`amap` に無いアドレスなら、そこから走査し直して埋める。

        **先頭からの走査が作った命令境界に、分岐先が乗るとは限りません**
        （`walk.sweep_from()`）。`trace()` は `resweep` でこれを回避しますが、
        テーブルの読み出しは `scan()` の中で `amap` を直接引くため、同じ手当てが
        必要です。無いまま `False` を返すと、**実在するテーブルが不成立になります**。

        Args:
            addr (int): 対象アドレス。

        Returns:
            bool: `amap` に載っていれば True。
        """
        if addr in self.amap:
            return True
        if not self.in_rom(addr):
            return False
        before = len(self.amap)
        for a, p in self.resweep(addr).items():
            self.amap.setdefault(a, p)
        if len(self.amap) != before:
            self.order = sorted(self.amap)
            self.index = {a: i for i, a in enumerate(self.order)}
        return addr in self.amap

    def plausible(self, addr, depth=PLAUSIBLE_DEPTH):
        """addr からコードとして素直に復号できるかを返す。

        `depth` 命令ぶん追い、途中で ROM の外へ出たり復号できない場所に当たったり
        しなければコードらしいとみなします。`RET` / `JP` で終われば、そこで True。

        Args:
            addr (int): 調べるアドレス（テーブルの要素の値など）。
            depth (int): 追う命令数の上限。

        Returns:
            bool: コードとして読めそうなら True。
        """
        if not self.in_rom(addr) or not self._ensure_mapped(addr):
            return False
        a = addr
        for _ in range(depth):
            p = self.amap.get(a)
            if p is None:
                return False
            continues, _target = classify_instruction(p["asm"])
            if not continues:
                return True  # RET/JP で素直に終端した
            a += len(p["opcode"])
            if not self.in_rom(a):
                return False
        return True

    def _consistent(self, addr, code, heads):
        """既に確定したコードの命令途中に着地していないかを返す。

        Args:
            addr (int): 調べるアドレス。
            code (set[int]): 現時点で確定している到達バイト集合。
            heads (set[int]): 現時点で確定している命令先頭集合。

        Returns:
            bool: 命令の途中でなければ True。まだ到達していないアドレスも True。
        """
        return not (addr in code and addr not in heads)

    def read_table(self, base, code, heads):
        """base から始まるジャンプテーブルを読み出す。

        dw 形式 (stride 2) と JP 命令の並び (stride 3) の両方を扱う。

        Args:
            base (int): テーブル基底アドレス。
            code (set[int]): 現時点で確定している到達バイト集合。
            heads (set[int]): 現時点で確定している命令先頭集合。

        Returns:
            tuple[list[int], int]: (エントリ列, stride)。不成立時は ([], 0)。
        """
        if not self.in_rom(base):
            return [], 0

        # JP nnnn の並び: テーブル自体が命令なので、スロット先頭をエントリにする
        if self.byte(base) == 0xC3:
            slots = []
            a = base
            while self.in_rom(a + 2) and self.byte(a) == 0xC3:
                target = self.byte(a + 1) | self.byte(a + 2) << 8
                if not self.plausible(target):
                    break
                if not self._consistent(target, code, heads):
                    break
                slots.append(a)
                a += 3
            return (slots, 3) if len(slots) >= MIN_TABLE else ([], 0)

        # dw LABEL の並び
        words = []
        a = base
        # テーブルより後ろを指す分岐先の最小値。そこに達したらテーブルの終わり。
        # **テーブルは自分の分岐先に食い込めない。** ハンドラがテーブルの直後に
        # 並ぶ配置（RST 直後に埋め込むテーブルで多い）では、この規則が無いと
        # ハンドラのバイトを語として読み続ける。
        #
        # **前を指す分岐先を混ぜてはいけない。** 混ぜると `a >= forward_min` が
        # 初回から成立してテーブルを 1 件で打ち切る。
        forward_min = None
        while self.in_rom(a + 1):
            if forward_min is not None and a >= forward_min:
                break
            w = self.byte(a) | self.byte(a + 1) << 8
            if w == 0:
                # 0x0000 はリセットベクタなので `plausible()` を通ってしまうが、
                # ディスパッチテーブルの要素としては詰め物である。テーブルの
                # 末尾に 0 埋めが続く配置で、それを要素として数えてしまう。
                break
            # 直前と同じ語なら打ち切る（0 埋めなどのデータ）。
            #
            # **離れた位置での重複は許す。** ディスパッチテーブルでは同じハンドラを
            # 複数の添字に割り当てるのが普通である。以前は `w in words` で
            # 「一度でも出た語」を打ち切っていたため、19 件のテーブルが 9 件目
            # （3 件目と同じハンドラ）で切れていた。
            if words and w == words[-1]:
                break
            if not self.plausible(w):
                break
            if not self._consistent(w, code, heads):
                break
            words.append(w)
            if w > base and (forward_min is None or w < forward_min):
                forward_min = w
            a += 2
        return (words, 2) if len(words) >= MIN_TABLE else ([], 0)

    def _backward(self, site, heads, window=BACK_WINDOW):
        """site の直前の到達済み命令を新しい順に返す（基本ブロック内で打ち切る）。

        Args:
            site (int): 遡り始めるアドレス（分岐命令の位置）。この命令自身は含めない。
            heads (set[int]): 到達済みの命令先頭集合。
            window (int): 集める命令数の上限。

        Returns:
            list[dict]: `amap` の命令（`"asm"` / `"opcode"` を持つ dict）。site に
                近いものが先。無条件 `RET` / `JP` に当たったら、その手前で止めます。
        """
        i = self.index.get(site)
        if i is None:
            return []
        res = []
        j = i - 1
        while j >= 0 and len(res) < window:
            a = self.order[j]
            j -= 1
            if a not in heads:
                continue
            p = self.amap[a]
            continues, _target = classify_instruction(p["asm"])
            if not continues:
                break  # 無条件 RET/JP を越えたら別ブロック
            res.append(p)
        return res

    # ------------------------------------------------- テーブル基底の逆算パターン

    def _bases_from_ram(self, ram):
        """RAM アドレス ram に書き込まれる定数ポインタを ROM 全体から探す。

        `LD hl,mmmm` → `LD (ram),hl` 形式と、
        `LD a,LL` → `LD (ram),a` / `LD a,HH` → `LD (ram+1),a` のバイト分割形式に対応。

        Args:
            ram (int): ポインタを置く RAM のアドレス。

        Returns:
            set[int]: 書き込まれる定数（テーブル基底の候補）。
        """
        bases = set()
        lo = hi = None
        for i, a in enumerate(self.order):
            asm = self.amap[a]["asm"]

            m = RE_ST_HL_IND.match(asm)
            if m and int(m.group(1), 16) == ram:
                for j in range(max(0, i - 6), i):
                    m2 = RE_LD_RR_IMM.match(self.amap[self.order[j]]["asm"])
                    if m2 and m2.group(1).lower() == "hl":
                        bases.add(int(m2.group(2), 16))

            m = RE_ST_A_IND.match(asm)
            if m:
                dst = int(m.group(1), 16)
                if dst in (ram, ram + 1):
                    for j in range(max(0, i - 3), i):
                        m2 = RE_LD_A_IMM.match(self.amap[self.order[j]]["asm"])
                        if not m2:
                            continue
                        if dst == ram:
                            lo = int(m2.group(1), 16)
                        else:
                            hi = int(m2.group(1), 16)
        if lo is not None and hi is not None:
            bases.add(hi << 8 | lo)
        return bases

    def _bases_near(self, site, heads):
        """site の直前から、テーブル基底になりうる定数を集める。

        `LD rr, nnnn` の即値と、`LD hl, (nnnn)` の参照先を拾います。参照先が RAM
        なら、そこへ書き込まれる定数を `_bases_from_ram()` で探します。

        Args:
            site (int): 分岐命令のアドレス。
            heads (set[int]): 到達済みの命令先頭集合。

        Returns:
            set[int]: テーブル基底の候補。確かめるのは `read_table()` です。
        """
        bases = set()
        for p in self._backward(site, heads):
            asm = p["asm"]

            m = RE_LD_RR_IMM.match(asm)
            if m:
                v = int(m.group(2), 16)
                if self.in_rom(v):
                    bases.add(v)

            m = RE_LD_HL_IND.match(asm)
            if m:
                ram = int(m.group(1), 16)
                if self.in_rom(ram):
                    bases.add(ram)  # ROM 内なら直接テーブル
                else:
                    bases |= self._bases_from_ram(ram)  # RAM 経由の間接
        return bases

    # ---------------------------------------------------------- 定型句カタログ

    def scan(self, code, heads):
        """到達済みコードからディスパッチ定型句を探し Finding のリストを返す。

        Args:
            code (set[int]): 到達済みのバイト集合（`trace()` の 1 つ目）。
            heads (set[int]): 到達済みの命令先頭集合（`trace()` の 2 つ目）。

        Returns:
            list[Finding]: 定型句ごとの抽出結果。同じ地点が複数の形で見つかる
                こともあります（重複は `run()` がまとめます）。
        """
        findings = []
        findings += self._p_indirect_jp(code, heads)
        findings += self._p_push_ret(code, heads)
        findings += self._p_sp_ret(code, heads)
        findings += self._p_inline_after_call(code, heads)
        findings += self._p_inline_after_rst(code, heads)
        findings += self._p_push_return(code, heads)
        findings += self._p_rst(code, heads)
        return findings

    def _emit(self, pattern, site, bases, code, heads):
        """基底候補群からテーブルを読み出して Finding 化する。

        Args:
            pattern (str): 定型句の識別子。
            site (int): 定型句を検出したアドレス。
            bases (set[int]): テーブル基底の候補。
            code (set[int]): 到達済みのバイト集合。
            heads (set[int]): 到達済みの命令先頭集合。

        Returns:
            list[Finding]: テーブルとして読めた候補ごとに 1 件。読めなければ空。
        """
        out = []
        for b in sorted(bases):
            targets, stride = self.read_table(b, code, heads)
            if targets:
                out.append(Finding(pattern, site, b, stride, targets))
        return out

    def _p_indirect_jp(self, code, heads):
        """JP (hl) / JP (ix) / JP (iy) 直前のテーブル引き。

        Args:
            code (set[int]): 到達済みのバイト集合。
            heads (set[int]): 到達済みの命令先頭集合。

        Returns:
            list[Finding]: `jp-indirect` の抽出結果。
        """
        out = []
        for a in sorted(heads):
            if not RE_JP_IND.match(self.amap[a]["asm"]):
                continue
            out += self._emit("jp-indirect", a, self._bases_near(a, heads), code, heads)
        return out

    def _p_push_ret(self, code, heads):
        """PUSH hl の直後の RET で分岐する形式。

        `PUSH hl` と `RET` の間に、先へ進む命令を 2 つまで挟めます。

        Args:
            code (set[int]): 到達済みのバイト集合。
            heads (set[int]): 到達済みの命令先頭集合。

        Returns:
            list[Finding]: `push-ret` の抽出結果。
        """
        out = []
        for a in sorted(heads):
            if self.amap[a]["asm"].strip().upper() != "PUSH HL":
                continue
            nxt = a + len(self.amap[a]["opcode"])
            # PUSH hl と RET の間に 2 命令まで許容
            for _ in range(3):
                p = self.amap.get(nxt)
                if p is None or nxt not in heads:
                    break
                if p["asm"].strip().upper() == "RET":
                    out += self._emit(
                        "push-ret", a, self._bases_near(a, heads), code, heads
                    )
                    break
                continues, _target = classify_instruction(p["asm"])
                if not continues:
                    break
                nxt += len(p["opcode"])
        return out

    def _p_sp_ret(self, code, heads):
        """LD sp,hl + RET/RETN によるタスク再開。

        復帰先は実行時のスタック内容なので静的には解決できない。
        取りこぼしの可能性がある箇所として報告のみ行う。

        Args:
            code (set[int]): 到達済みのバイト集合。
            heads (set[int]): 到達済みの命令先頭集合。

        Returns:
            list[Finding]: `sp-ret` の報告。`targets` は空で、`note` に理由を持つ。
        """
        out = []
        for a in sorted(heads):
            if self.amap[a]["asm"].strip().upper() != "LD SP, HL":
                continue
            nxt = a + len(self.amap[a]["opcode"])
            for _ in range(12):
                p = self.amap.get(nxt)
                if p is None or nxt not in heads:
                    break
                if p["asm"].strip().upper() in ("RET", "RETN", "RETI"):
                    out.append(
                        Finding(
                            "sp-ret",
                            a,
                            # 出力は英語で揃える。他の CLI メッセージ・ヘルプ・
                            # エラーはすべて英語なので、ここだけ日本語だと
                            # 日本語を読まない利用者が面食らう。
                            note=(
                                "task resume: return address depends on the "
                                "runtime stack; cannot be resolved statically"
                            ),
                        )
                    )
                    break
                continues, _target = classify_instruction(p["asm"])
                if not continues:
                    break
                nxt += len(p["opcode"])
        return out

    def _p_inline_after_call(self, code, heads):
        """CALL 直後にテーブルを埋め込む形式。

        呼ばれる側が POP hl / POP de で戻りアドレスを取り出していれば、
        CALL 命令の直後がテーブル本体になる。

        Args:
            code (set[int]): 到達済みのバイト集合。
            heads (set[int]): 到達済みの命令先頭集合。

        Returns:
            list[Finding]: `inline-after-call` の抽出結果。
        """
        pop_routines = {
            a
            for a in heads
            if self.amap[a]["asm"].strip().upper() in ("POP HL", "POP DE")
        }
        if not pop_routines:
            return []

        out = []
        for a in sorted(heads):
            m = RE_CALL.match(self.amap[a]["asm"])
            if not m:
                continue
            if int(m.group(2), 16) not in pop_routines:
                continue
            base = a + len(self.amap[a]["opcode"])
            out += self._emit("inline-after-call", a, {base}, code, heads)
        return out

    def _rst_dispatches_via_table(self, vec, heads):
        """RST のベクタ先が「戻りアドレスを取り出してテーブルで飛ぶ」形かを返す。

        `inline-after-call` と同じ判定をベクタ先に当てはめたもの。`POP hl`
        （`ix` / `iy` も可）で戻りアドレスを取り、`JP (hl)` に至れば、その RST の
        直後がテーブル本体になる。

        Args:
            vec (int): RST のベクタアドレス。
            heads (set[int]): 到達済みの命令先頭集合。

        Returns:
            bool: テーブル分岐なら True。
        """
        a = vec
        popped = False
        for _ in range(RST_BODY_WINDOW):
            p = self.amap.get(a)
            if p is None or a not in heads:
                return False
            if RE_POP_PTR.match(p["asm"].strip()):
                popped = True
            if RE_JP_IND.match(p["asm"].strip()):
                return popped
            continues, _target = classify_instruction(p["asm"])
            if not continues:
                return False
            a += len(p["opcode"])
        return False

    def _p_inline_after_rst(self, code, heads):
        """RST 直後にテーブルを埋め込む形式。

        `classify_instruction()` は RST を「呼んで次の命令へ戻る」ものとして
        扱うため、直後のテーブルを命令として読んでしまう。ベクタ先が戻りアドレスを
        取り出す形なら戻ってこないので、`no_fallthrough` に site を積んで
        追跡側に伝える（`trace()` の `stop_after`）。これでテーブルのバイトは
        コードにならず、自然にデータへ落ちる。

        Args:
            code (set[int]): 到達済みのバイト集合。
            heads (set[int]): 到達済みの命令先頭集合。

        Returns:
            list[Finding]: `inline-after-rst` の抽出結果。テーブルが読めなかった
                RST も `no_fallthrough` には積みます（戻り値には現れません）。
        """
        out = []
        verdict = {}
        for a in sorted(heads):
            m = RE_RST.match(self.amap[a]["asm"].strip())
            if not m:
                continue
            vec = int(m.group(1), 0)
            if vec not in verdict:
                verdict[vec] = self.in_rom(vec) and self._rst_dispatches_via_table(
                    vec, heads
                )
            if not verdict[vec]:
                continue
            # **テーブルが読めたかに関わらず積む。** 戻ってこないことはベクタ先の
            # 形だけで決まる。成否に連動させると循環する: テーブルを読むには
            # 直後のバイトがコードでない必要があり、コードでなくするには
            # 「戻らない」と決める必要がある。実際にこれで 2 つのテーブルが
            # 読めなかった（先頭の語が、命令として読まれたバイトの途中を指す）。
            self.no_fallthrough.add(a)
            base = a + len(self.amap[a]["opcode"])
            out += self._emit("inline-after-rst", a, {base}, code, heads)
        return out

    def _p_push_return(self, code, heads):
        """`LD hl, nn` の直後に `PUSH hl` がある形（戻り先をスタックへ積む）。

        `push-ret`（`PUSH hl` の直後に `RET`）とは別の形で、積んでから途中に
        分岐を挟み、あとで `RET` で戻る。積んだ `nn` がそのまま戻り先になる。

        **積んだ値がアドレスとは限りません。** 実 ROM には

            LD hl, 0x0100 / PUSH hl / POP hl / DEC hl / LD a,h / OR l / JP nz,…

        という遅延ループがあり、この 0x0100 は**回数**です。`plausible()` だけでは
        通ってしまい（コードとして素直に読めてしまう）、実 ROM 2 本で誤検出
        しました。すぐ `POP` で戻しているかを見て弾きます。
        **積んだ値を戻すなら、それは戻り先ではない。**

        それでも誤検出の余地は残るため、根拠を `# auto-entry:` の行に出して
        利用者が判断できるようにしている。

        Args:
            code (set[int]): 到達済みのバイト集合。
            heads (set[int]): 到達済みの命令先頭集合。

        Returns:
            list[Finding]: `push-return` の抽出結果。`targets` は積んだ `nn` 1 つ。
        """
        out = []
        for a in sorted(heads):
            m = RE_LD_HL_IMM.match(self.amap[a]["asm"].strip())
            if not m:
                continue
            nxt = a + len(self.amap[a]["opcode"])
            p = self.amap.get(nxt)
            if p is None or nxt not in heads:
                continue
            if p["asm"].strip().upper() != "PUSH HL":
                continue
            target = int(m.group(1), 16)
            if not (self.in_rom(target) and self.plausible(target)):
                continue
            if not self._pushed_value_is_kept(nxt + len(p["opcode"]), heads):
                continue
            out.append(Finding("push-return", a, targets=[target]))
        return out

    def _pushed_value_is_kept(self, after, heads):
        """積んだ値が積まれたまま残るか（すぐ `POP` で戻されないか）を返す。

        `POP` に当たったら False。無条件分岐・`RET`・テーブル分岐（`RST` を
        `no_fallthrough` に積んだもの）まで届いたら True。**戻り先として積んだなら、
        その先の `RET` で使われるので自分では戻さない**、という見分け方である。

        Args:
            after (int): `PUSH` の直後のアドレス。
            heads (set[int]): 到達済みの命令先頭集合。

        Returns:
            bool: 積まれたままなら True。
        """
        a = after
        for _ in range(PUSH_RETURN_WINDOW):
            p = self.amap.get(a)
            if p is None or a not in heads:
                return False
            if p["asm"].strip().upper().startswith("POP"):
                return False
            continues, _target = classify_instruction(p["asm"])
            if not continues or a in self.no_fallthrough:
                return True
            a += len(p["opcode"])
        return True

    def _p_rst(self, code, heads):
        """到達コード中に RST n が実在する場合のみベクタを採用する。

        Args:
            code (set[int]): 到達済みのバイト集合。
            heads (set[int]): 到達済みの命令先頭集合。

        Returns:
            list[Finding]: `rst-vector` の抽出結果。`targets` はベクタのアドレス 1 つ。
        """
        out = []
        for a in sorted(heads):
            m = RE_RST.match(self.amap[a]["asm"])
            if not m:
                continue
            vec = int(m.group(1), 0)
            if self.in_rom(vec):
                out.append(Finding("rst-vector", a, targets=[vec]))
        return out

    # ------------------------------------------------------------------- 反復

    def default_vectors(self):
        """初期エントリとして採用する固定ベクタを返す。

        NMI は暗号化 ROM でも復号後の命令列で判定する。生バイトで filler 判定を
        行うと、暗号化 ROM で RST ベクタが軒並み誤採用されるため。

        Returns:
            set[int]: 開始アドレス（RESET）と、コードらしければ NMI のアドレス。
        """
        entries = {self.start}
        nmi = VECTOR_ALIASES["NMI"]
        if self.plausible(nmi) and self.amap[nmi]["asm"].strip().upper() != "NOP":
            entries.add(nmi)
        return entries

    def run(self, seed_entries=None, max_iter=8):
        """不動点に達するまでエントリ抽出を反復する。

        Args:
            seed_entries (iterable[int] | None): 追加の初期エントリ。
                固定ベクタ（RESET / コードらしければ NMI）は常に含まれる。
            max_iter (int): 最大反復回数。

        Returns:
            tuple[set[int], list[Finding]]: (エントリ集合, 抽出結果)。
        """
        entries = self.default_vectors() | set(seed_entries or ())

        findings = {}
        for _ in range(max_iter):
            code, heads = self.trace(entries)
            # 次の `trace()` の結果を変えるものは 2 つある。エントリが増えたか、
            # **`no_fallthrough` が増えたか**。後者を見ないと 1 回早く収束する。
            # 「戻らない RST」が増えると直後のバイトがコードでなくなり、そこで
            # 初めて読めるテーブルがある。実 ROM でテーブル 2 個を落としていた。
            stops = len(self.no_fallthrough)
            new = set()
            for f in self.scan(code, heads):
                findings[(f.pattern, f.site, f.base)] = f
                new |= set(f.targets)
            if new <= entries and len(self.no_fallthrough) == stops:
                break
            entries |= new

        return entries, sorted(findings.values(), key=lambda f: (f.site, f.base or 0))
