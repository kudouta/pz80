#!/usr/bin/env python3

import argparse
import ast
import contextlib
import importlib
import importlib.util
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field

from pz80 import __about__, asm, disasm
from pz80._vectors import parse_entry
from pz80.walk import walk as _walk


def _load_config(config_path):
    """Python設定ファイルをモジュールとして読み込む。

    Args:
        config_path (str): ファイルパスまたはモジュール名。

    Returns:
        module: 読み込んだモジュール。失敗時は sys.exit。
    """
    if os.path.exists(config_path):
        try:
            spec = importlib.util.spec_from_file_location("config_module", config_path)
            if spec and spec.loader:
                m = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(m)
                return m
        except Exception as e:
            print(
                f"Error: Failed to load config file '{config_path}': {e}",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        module_name = config_path.removesuffix(".py")
        if os.getcwd() not in sys.path:
            sys.path.insert(0, os.getcwd())
        try:
            return importlib.import_module(module_name)
        except ModuleNotFoundError:
            print(f"Error: Config module '{config_path}' not found.", file=sys.stderr)
            sys.exit(1)


def _load_images_from_bins(bins):
    """bins設定 [(path, addr), ...] から images リストと valid_ranges を構築する。

    Args:
        bins (list[tuple[str, int]]): (ファイルパス, ロードアドレス) のリスト。

    Returns:
        tuple[list[int], list[list[int]]]: (images, valid_ranges)。
    """
    images = []
    valid_ranges = []
    try:
        for path, addr in bins:
            if addr < 0:
                print(f"Error: Address must be non-negative: {addr}", file=sys.stderr)
                sys.exit(1)
            with open(path, "rb") as f:
                data = f.read()
            end = addr + len(data) - 1
            if end >= 0x10000:
                print(
                    "Error: ROM extends beyond 64KB limit (Z80 address space).",
                    file=sys.stderr,
                )
                sys.exit(1)
            if end + 1 > len(images):
                images += [0] * (end + 1 - len(images))
            images[addr : addr + len(data)] = list(data)
            valid_ranges.append([addr, end])
    except FileNotFoundError:
        print(f"Error: ROM file not found: {path}", file=sys.stderr)
        sys.exit(1)
    return images, valid_ranges


def _load_images_from_input(paths):
    """-i で指定されたファイル群を先頭から連結して読み込む。

    Args:
        paths (list[str]): 入力ファイルパスのリスト。

    Returns:
        list[int]: アドレス 0 基点のイメージ。
    """
    images = []
    for path in paths:
        try:
            with open(path, mode="rb") as f:
                data = f.read()
        except FileNotFoundError:
            print(f"Error: Input file not found: {path}", file=sys.stderr)
            sys.exit(1)
        if len(images) + len(data) > 0x10000:
            print(
                "Error: Total input size exceeds 64KB limit (Z80 address space).",
                file=sys.stderr,
            )
            sys.exit(1)
        images += list(data)
    return images


@dataclass
class CliConfig:
    """config ファイルと CLI 引数を解釈した結果。

    設定キーの意味をここ 1 箇所で確定させる。各コマンドハンドラは解釈をせず、
    この結果を自分のオブジェクトへ配線するだけにする。

    Attributes:
        images (list[int]): アドレス 0 基点のイメージ。
        valid_ranges (list[list[int]] | None): 有効アドレス範囲。bins 指定時のみ。
        start (int): 開始アドレス（walk ではメインエントリポイントも兼ねる）。
        entries (list): 追加エントリポイント。未 parse の生値（シンボル名も可）。
        m1_handler (Callable | None): M1サイクル復号ハンドラー。
        datamap (list | None): データ領域（disasm のみ）。
        strmap (tuple | None): キャラクターコード表（disasm のみ）。
        label_names (dict | None): `{アドレス: 名前}`（disasm のみ）。
        raw_operand (list | None): 16 ビットオペランドを数値のまま出す
            命令の番地（disasm のみ）。
        comments (dict | None): 出力へ出すコメント（disasm のみ）。
            ラベルが `L_0066@NMI` の形になる。
        equ_names (dict | None): `{アドレス: 名前 | {"r": ..., "w": ...}}`（disasm のみ）。
            逆アセンブル範囲外の定数に `EQU` で名前を付ける。
        output (Callable | None): カスタム出力関数（disasm のみ）。
    """

    images: list = field(default_factory=list)
    valid_ranges: list | None = None
    start: int = 0
    entries: list = field(default_factory=list)
    m1_handler: Callable | None = None
    datamap: list | None = None
    strmap: tuple | None = None
    label_names: dict | None = None
    raw_operand: list | None = None
    comments: dict | None = None
    equ_names: dict | None = None
    output: Callable | None = None

    @property
    def size(self):
        """イメージの有効長。"""
        return len(self.images)


def resolve_config(args):
    """config ファイルと CLI 引数をマージして解釈する。

    設定キーの解釈はこの関数だけが行う。CLI 引数が config より優先される
    （`start`）か、マージされる（`entry`）かもここで確定する。

    Args:
        args (argparse.Namespace): コマンドライン引数。
            `input` / `config` / `start` / `entry` を参照する（無い属性は未指定扱い）。

    Returns:
        CliConfig: 解釈結果。
    """
    cfg = CliConfig()
    cfg.entries = list(getattr(args, "entry", None) or [])
    start = getattr(args, "start", None)  # None の場合は後で解決

    config_path = getattr(args, "config", None)
    if config_path is not None:
        m = _load_config(config_path)
        if m:
            if hasattr(m, "bins"):
                cfg.images, cfg.valid_ranges = _load_images_from_bins(m.bins)
            if start is None and hasattr(m, "start"):
                start = m.start
            if hasattr(m, "entry"):
                # config の entry を先に、CLI の -e を後に（両方有効）
                cfg.entries = list(m.entry) + cfg.entries
            if hasattr(m, "m1_handler"):
                cfg.m1_handler = m.m1_handler
            if hasattr(m, "data"):
                cfg.datamap = m.data
            if hasattr(m, "chr"):
                cfg.strmap = m.chr
            if hasattr(m, "labels"):
                cfg.label_names = dict(m.labels)
            if hasattr(m, "equ"):
                cfg.equ_names = dict(m.equ)
            if hasattr(m, "raw_operand"):
                cfg.raw_operand = list(m.raw_operand)
            if hasattr(m, "comments"):
                cfg.comments = dict(m.comments)
            if hasattr(m, "output"):
                cfg.output = m.output

    cfg.start = start or 0

    # -i から読み込む（config に bins がない場合）
    if not cfg.images:
        if not getattr(args, "input", None):
            print(
                "Error: No input specified. Use -i or provide bins in -c config.",
                file=sys.stderr,
            )
            sys.exit(1)
        cfg.images = _load_images_from_input(args.input)

    return cfg


# -D のシンボル名。ラベルと同じく英字か @ で始まり、空白や区切り記号を含まない。
_RE_DEFINE_NAME = re.compile(r"^[A-Za-z@][^\s:=,{}()]*$")


def _parse_define_pair(text):
    """`NAME=VALUE` 形式の 1 件を解釈する。

    値を省略した場合は 1 とする（`gcc -DFOO` と同じ）。
    値は `int(v, 0)` で解釈するため 10進・`0x`・`0b` が使える。

    Args:
        text (str): `NAME=VALUE` または `NAME`。

    Returns:
        tuple[str, int]: (シンボル名, 値)。

    Raises:
        ValueError: 名前が空・書式不正、または値が整数として解釈できない場合。
    """
    name, sep, raw = text.partition("=")
    name = name.strip()
    if not name:
        raise ValueError(f"Invalid -D value (empty symbol name): {text}")
    if not _RE_DEFINE_NAME.match(name):
        raise ValueError(f"Invalid -D symbol name: {name!r}")

    if not sep:
        return name, 1  # 値の省略は 1 扱い

    raw = raw.strip()
    try:
        return name, int(raw, 0)
    except ValueError as e:
        raise ValueError(
            f"Invalid -D value for '{name}' (expected an integer): {raw}"
        ) from e


def _parse_define_dict(text):
    """Python 辞書リテラル 1 件を解釈する。

    `ast.literal_eval` で解釈するためコードは実行されない。

    Args:
        text (str): 辞書リテラル。例: `{"DEBUG": 1}`

    Returns:
        dict: シンボル定義。

    Raises:
        ValueError: 辞書として解釈できない、キーが文字列でない、
            値が整数でない場合。
    """
    try:
        parsed = ast.literal_eval(text)
    except (ValueError, SyntaxError) as e:
        raise ValueError(f"Invalid -D value (expected a Python dict): {text}") from e

    if not isinstance(parsed, dict):
        raise ValueError(f"Invalid -D value (expected a Python dict): {text}")

    for key, value in parsed.items():
        if not isinstance(key, str):
            raise ValueError(f"Invalid -D key (expected a string): {key!r}")
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(
                f"Invalid -D value for '{key}' (expected an integer): {value!r}"
            )
    return parsed


def parse_defines(values):
    """`-D` で渡されたシンボル定義を解釈してマージする。

    2 つの書式を受け付ける。

    * `NAME=VALUE` / `NAME` — 引用符が不要なのでどのシェルでもそのまま書ける。
      手打ちではこちらを想定している。値を省略すると 1。
    * `{"NAME": VALUE, ...}` — Python 辞書リテラル。多数まとめて渡すとき用。

    複数指定された場合は左から順にマージし、同じキーは後勝ちとする。

    Args:
        values (list[str] | None): `-D` の値のリスト。
            例: ["DEBUG=1", '{"TRACE": 1}']

    Returns:
        dict: マージ済みのシンボル定義。

    Raises:
        ValueError: どちらの書式としても解釈できない場合。
    """
    defines = {}
    for text in values or []:
        stripped = text.strip()
        if stripped.startswith("{"):
            defines.update(_parse_define_dict(stripped))
        else:
            name, value = _parse_define_pair(stripped)
            defines[name] = value
    return defines


def command_asm(args):
    """アセンブラコマンドハンドラ。

    Args:
        args (argparse.Namespace): コマンドライン引数。
    """

    ope = asm.Asm()
    try:
        defines = parse_defines(getattr(args, "define", None))
        inp = ope.exec(args.file, defines=defines)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    mode = "wb"
    if args.size is not None:
        try:
            size = int(args.size, 0)
            if size < 0:
                print(
                    f"Error: Output size cannot be negative: {args.size}",
                    file=sys.stderr,
                )
                sys.exit(1)

            with open(args.output, "wb") as f:
                f.write(b"\x00" * size)
            mode = "rb+"
        except ValueError:
            print(f"Error: Invalid size format: {args.size}", file=sys.stderr)
            sys.exit(1)

    # write object
    try:
        with open(args.output, mode) as f:
            for p in inp:
                if not p.get("opcode"):
                    continue

                base = p.get("base", 0)
                if base < 0:
                    base = 0

                address = base + p.get("offset", 0)
                if f.tell() != address:
                    f.seek(address)

                f.write(bytes(p["opcode"]))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def command_disasm(args):
    """逆アセンブラコマンドハンドラ。

    Args:
        args (argparse.Namespace): コマンドライン引数。
    """
    cfg = resolve_config(args)

    ope = disasm.Disasm()
    if cfg.datamap is not None:
        ope.datamap = cfg.datamap
    if cfg.strmap is not None:
        ope.cpu.strmap = cfg.strmap
    if cfg.m1_handler is not None:
        ope.m1_handler = cfg.m1_handler
    if cfg.entries:
        # walk と同じ entry をラベル付与に流用（NMI 等にラベルを付ける）
        ope.label_addresses = [parse_entry(e) for e in cfg.entries]
    if cfg.label_names is not None:
        # config の labels は {アドレス: 名前}。キーの解決と名前の検証は
        # Disasm.label_names のセッターが行う（不正な名前はここで弾かれる）。
        ope.label_names = cfg.label_names
    if cfg.equ_names is not None:
        ope.equ_names = cfg.equ_names
    if cfg.raw_operand is not None:
        ope.raw_operand = cfg.raw_operand
    if cfg.comments is not None:
        ope.comments = cfg.comments

    if cfg.valid_ranges is not None:
        # bins の隙間は walk と同じく出力から除外する。実在しないバイトを
        # `nop` の列として読んでいたのを揃えた（詳細は Disasm.valid_ranges）。
        ope.valid_ranges = cfg.valid_ranges

    output = cfg.output or output_default
    images, size, start = cfg.images, cfg.size, cfg.start

    for p in ope.datamap:
        if p[0] > p[1]:
            print(
                f"Error: Invalid data range in config: start=0x{p[0]:04X} > end=0x{p[1]:04X}",
                file=sys.stderr,
            )
            sys.exit(1)

    # 逆アセンブル
    out = ope.exec(start, images[start:], size - start)

    if args.output is not None:
        # asm.exec() はソースを UTF-8 で読むため、逆アセンブル結果も UTF-8 で書く。
        # encoding 未指定だとロケール依存になり、chr（strmap）に非ASCIIを含む場合に
        # disasm -o → asm -f の往復が壊れる。
        with (
            open(args.output, "w", encoding="utf-8") as f,
            contextlib.redirect_stdout(f),
        ):
            output(out, args.nodump)
    else:
        output(out, args.nodump)


def output_default(dis, sw):
    """逆アセンブラのデフォルト出力関数。

    Args:
        dis (list): 逆アセンブルデータリスト。
        sw (bool): ダンプなしフラグ (True: アドレスとオペコードを隠す)。
    """
    # ラベル桁は実データの最長に合わせる。以前は 5 のベタ書きで、逆アセンブラが
    # 出す `L_xxxx:` の 7 文字を常に下回っていたため、ラベルのある行だけ
    # ニーモニックが 1 桁右へずれていた。`labels` で名前を付けると差が開くので
    # 実測に切り替える。
    lbsize = max((len(p.get("label", "")) for p in dis), default=0)

    for p in dis:
        if sw:  # ダンプなし
            label = p.get("label", "")
            if label:
                print(label)
            if p.get("asm"):
                indent = "    " if p.get("opcode") else ""
                print(f"{indent}{p['asm']}")
        else:
            # EQU の定義行は番地を持たない（出力の先頭に置く前書き）。
            # 0x0000 と表示すると 0 番地の行と紛らわしいので空欄にする。
            addr_str = f"0x{p['address']:04X}" if "address" in p else " " * 6
            op_bytes = p.get("opcode", [])
            op_str = " ".join(f"{b:02X}" for b in op_bytes)
            label_str = p.get("label", "")
            asm_str = p.get("asm", "")
            print(f"{addr_str} {op_str:<12} {label_str:<{lbsize + 1}} {asm_str}")


def _auto_detect_entries(cfg, seed_entries):
    """ディスパッチ定型句からエントリポイントを自動抽出します。

    抽出根拠を `# auto-entry:` のコメント行として標準出力に書きます。Python の
    コメントなので、`data = [...]` ごと config ファイルに保存できます。
    誤ったエントリはデータをコードとして誤認させるため、根拠を必ず残します。

    Args:
        cfg (CliConfig): 解釈済みの設定。
        seed_entries (list): 初期エントリ（未 parse の生値）。

    Returns:
        list[int]: 抽出したエントリポイントのソート済みリスト。
    """
    from pz80._auto_entry import AutoEntry

    try:
        seed = [parse_entry(e) for e in seed_entries]
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    ae = AutoEntry(
        cfg.images,
        start=cfg.start,
        m1_handler=cfg.m1_handler,
        valid_ranges=cfg.valid_ranges,
    )
    found, findings = ae.run(seed_entries=seed)

    for f in findings:
        print(f"# auto-entry: {f.format()}")
    entry_opts = " ".join(f"-e 0x{e:04X}" for e in sorted(found))
    print(f"# auto-entry: entry = {entry_opts}")
    return sorted(found), set(ae.no_fallthrough)


def command_walk(args):
    """バイナリウォークコマンドハンドラ。CFGトレースによりデータ領域を検出する。

    Args:
        args (argparse.Namespace): コマンドライン引数。
    """
    cfg = resolve_config(args)
    images, start = cfg.images, cfg.start
    valid_ranges, m1_handler = cfg.valid_ranges, cfg.m1_handler
    extra_entries = cfg.entries

    stop_after = None
    if getattr(args, "auto_entry", False):
        extra_entries, stop_after = _auto_detect_entries(cfg, extra_entries)

    unresolved = set()
    try:
        regions = _walk(
            images,
            start=start,
            extra_entries=extra_entries or None,
            valid_ranges=valid_ranges,
            m1_handler=m1_handler,
            unresolved=unresolved,
            stop_after=stop_after,
        )
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    _report_unresolved(unresolved)

    print("data = [")
    for r in regions:
        print(f"    [0x{r[0]:04X}, 0x{r[1]:04X}],")
    print("]")


def _report_unresolved(unresolved):
    """復号できなかった分岐先を `# unresolved:` のコメント行で報告します。

    **正常なら何も出ません。** 実 ROM で測ると 0 件になります（手書きの Z80
    コードは ROM の外へ分岐しない）。出たら `bins` の指定漏れか ROM ファイルの
    欠落を疑う、という位置づけの報告です。

    Python のコメントなので、`data = [...]` ごと設定ファイルへ保存できます
    （`# auto-entry:` と同じ扱い）。

    Args:
        unresolved (set[int]): 追跡を打ち切ったアドレス。
    """
    if not unresolved:
        return
    listed = " ".join(f"0x{a:04X}" for a in sorted(unresolved))
    print(f"# unresolved: {listed}")
    print("# unresolved: branch targets above could not be decoded.")
    print("# unresolved: a ROM file may be missing from bins, or an entry is wrong.")


def _add_disasm_parser(subparsers):
    """disasm サブコマンドの引数を定義します。

    Args:
        subparsers: add_subparsers() の戻り値。
    """
    p = subparsers.add_parser("disasm", help="Z80 disassembler")
    p.add_argument(
        "-i",
        "--input",
        action="append",
        help="input image file (specify -i multiple times for multiple files)",
    )
    p.add_argument("-c", "--config", help="config file (Python module, see README)")
    p.add_argument(
        "-s", "--start", type=lambda x: int(x, 0), default=None, help="start address"
    )
    p.add_argument("-n", "--nodump", help="remove dump info", action="store_true")
    p.add_argument("-o", "--output", help="output file")
    p.set_defaults(handler=command_disasm)


def _add_walk_parser(subparsers):
    """walk サブコマンドの引数を定義します。

    Args:
        subparsers: add_subparsers() の戻り値。
    """
    p = subparsers.add_parser(
        "walk", help="Detect data regions in binary via CFG tracing"
    )
    p.add_argument(
        "-i",
        "--input",
        action="append",
        help="input binary file (specify -i multiple times for multiple files)",
    )
    p.add_argument("-c", "--config", help="config file (Python module, see README)")
    p.add_argument(
        "-s",
        "--start",
        type=lambda x: int(x, 0),
        default=None,
        help="start address (default: 0x0000)",
    )
    p.add_argument(
        "-e",
        "--entry",
        action="append",
        metavar="ADDR_OR_SYMBOL",
        help="additional entry point (address or: RESET/RST0-7/IM1/NMI)",
    )
    p.add_argument(
        "--auto-entry",
        action="store_true",
        help="auto-detect entry points from dispatch idioms (jump tables etc.)",
    )
    p.set_defaults(handler=command_walk)


def _add_asm_parser(subparsers):
    """asm サブコマンドの引数を定義します。

    Args:
        subparsers: add_subparsers() の戻り値。
    """
    p = subparsers.add_parser("asm", help="Z80 assembler")
    p.add_argument("-f", "--file", required=True, help="asm file")
    p.add_argument("-o", "--output", required=True, help="output file(bin)")
    p.add_argument("-s", "--size", help="*option* : output file(bin) size")
    p.add_argument(
        "-D",
        "--define",
        action="append",
        metavar="DEFINE",
        help="symbol definition for conditional assembly (repeatable, merged left to "
        "right). NAME=VALUE, NAME (=1), or a Python dict. "
        "example: -D DEBUG=1",
    )
    p.set_defaults(handler=command_asm)


def build_parser():
    """CLI の引数パーサを構築します。

    サブコマンドの追加順が `--help` の表示順になります。

    Returns:
        argparse.ArgumentParser: 構築済みのパーサ。
    """
    parser = argparse.ArgumentParser(
        description=f"Z80 assembler & disassembler v{__about__.__version__}"
    )
    subparsers = parser.add_subparsers()
    _add_disasm_parser(subparsers)
    _add_walk_parser(subparsers)
    _add_asm_parser(subparsers)
    return parser


def main():
    """CLIエントリーポイント。コマンドライン引数を解析してサブコマンドへ振り分けます。"""
    parser = build_parser()
    args = parser.parse_args()

    if hasattr(args, "handler"):
        args.handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
