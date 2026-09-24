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
# `L_3FE0@MSG_TABLE` の両方を受ける。捕捉群は 1 つだけなので、どちらの綴りでも
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
RE_RST = re.compile(r"^RST\s+(0x[0-9A-Fa-f]+|\d+)$", re.I)
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
        self.pattern = pattern
        self.site = site
        self.base = base
        self.stride = stride
        self.targets = targets or []
        self.note = note

    def format(self):
        """1 行の説明文を返す。"""
        if self.base is None:
            return f"[{self.pattern}] @0x{self.site:04X} {self.note}"
        targets = " ".join(f"0x{t:04X}" for t in self.targets)
        return (
            f"[{self.pattern}] @0x{self.site:04X} table=0x{self.base:04X} "
            f"stride={self.stride} -> {targets}"
        )

    def __repr__(self):
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
        self.data = list(data)
        self.size = len(self.data)
        self.start = 0 if valid_ranges is not None else start
        self.valid_ranges = valid_ranges

        self.m1_handler = m1_handler
        self.amap = build_addr_map(self.data, start, m1_handler, valid_ranges)
        self.order = sorted(self.amap)
        self.index = {a: i for i, a in enumerate(self.order)}

    # ------------------------------------------------------------------ 基本部品

    def in_rom(self, addr):
        """アドレスが ROM 範囲（かつ valid_ranges 内）かを返す。"""
        if not (self.start <= addr < self.start + self.size):
            return False
        if self.valid_ranges is None:
            return True
        return any(r[0] <= addr <= r[1] for r in self.valid_ranges)

    def byte(self, addr):
        """アドレスの生バイトを返す。範囲外なら None。"""
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
        """
        before = len(self.amap)
        result = trace(self.amap, entries, resweep=self.resweep)
        if len(self.amap) != before:
            self.order = sorted(self.amap)
            self.index = {a: i for i, a in enumerate(self.order)}
        return result

    def plausible(self, addr, depth=PLAUSIBLE_DEPTH):
        """addr からコードとして素直に復号できるかを返す。"""
        if not self.in_rom(addr) or addr not in self.amap:
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
        """既に確定したコードの命令途中に着地していないかを返す。"""
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
        while self.in_rom(a + 1):
            w = self.byte(a) | self.byte(a + 1) << 8
            if w in words:
                break  # 同じ語の反復は 0 埋め等のデータとみなす
            if not self.plausible(w):
                break
            if not self._consistent(w, code, heads):
                break
            words.append(w)
            a += 2
        return (words, 2) if len(words) >= MIN_TABLE else ([], 0)

    def _backward(self, site, heads, window=BACK_WINDOW):
        """site の直前の到達済み命令を新しい順に返す（基本ブロック内で打ち切る）。"""
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
        """RAM 番地 ram に書き込まれる定数ポインタを ROM 全体から探す。

        `LD hl,mmmm` → `LD (ram),hl` 形式と、
        `LD a,LL` → `LD (ram),a` / `LD a,HH` → `LD (ram+1),a` のバイト分割形式に対応。
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
        """site の直前から、テーブル基底になりうる定数を集める。"""
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
        """到達済みコードからディスパッチ定型句を探し Finding のリストを返す。"""
        findings = []
        findings += self._p_indirect_jp(code, heads)
        findings += self._p_push_ret(code, heads)
        findings += self._p_sp_ret(code, heads)
        findings += self._p_inline_after_call(code, heads)
        findings += self._p_rst(code, heads)
        return findings

    def _emit(self, pattern, site, bases, code, heads):
        """基底候補群からテーブルを読み出して Finding 化する。"""
        out = []
        for b in sorted(bases):
            targets, stride = self.read_table(b, code, heads)
            if targets:
                out.append(Finding(pattern, site, b, stride, targets))
        return out

    def _p_indirect_jp(self, code, heads):
        """JP (hl) / JP (ix) / JP (iy) 直前のテーブル引き。"""
        out = []
        for a in sorted(heads):
            if not RE_JP_IND.match(self.amap[a]["asm"]):
                continue
            out += self._emit("jp-indirect", a, self._bases_near(a, heads), code, heads)
        return out

    def _p_push_ret(self, code, heads):
        """PUSH hl の直後の RET で分岐する形式。"""
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

        呼ばれる側が POP hl / POP de で戻り番地を取り出していれば、
        CALL 命令の直後がテーブル本体になる。
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

    def _p_rst(self, code, heads):
        """到達コード中に RST n が実在する場合のみベクタを採用する。"""
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
            new = set()
            for f in self.scan(code, heads):
                findings[(f.pattern, f.site, f.base)] = f
                new |= set(f.targets)
            if new <= entries:
                break
            entries |= new

        return entries, sorted(findings.values(), key=lambda f: (f.site, f.base or 0))
