#!/usr/bin/env python3

import argparse
import importlib
import importlib.util
import os
import sys

from pz80 import __about__, asm, disasm

# version


def command_asm(args):
    """アセンブラコマンドハンドラ。

    Args:
        args (argparse.Namespace): コマンドライン引数。
    """

    ope = asm.Asm()
    try:
        inp = ope.exec(args.file)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    mode = "wb"
    if args.size is not None:
        try:
            size = int(args.size, 0)
            if size < 0:
                print(f"Error: Output size cannot be negative: {args.size}", file=sys.stderr)
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
    # ---- ここからメイン------------------------------------

    # raw file読み込み
    images = [0 for _ in range(0x10000)]
    try:
        size = 0
        for r in args.input:
            with open(r, mode="rb") as f:
                try:
                    data = f.read()
                    length = len(data)
                    if size + length > 0x10000:
                        print("Error: Total input size exceeds 64KB limit (Z80 address space).", file=sys.stderr)
                        sys.exit(1)
                        
                    images[size : size + length] = data
                    size += length
                except EOFError:
                    print(f"Error: Failed to read file: {r}", file=sys.stderr)
                    sys.exit(1)

    except FileNotFoundError:
        print(f"Error: Input file not found: {r}", file=sys.stderr)
        sys.exit(1)

    # output関数設定
    output = output_default

    # 設定ファイル読み込み
    ope = disasm.Disasm()
    if args.config is not None:
        m = None
        # ファイルパスとして存在する場合は直接ロード
        if os.path.exists(args.config):
            try:
                spec = importlib.util.spec_from_file_location("config_module", args.config)
                if spec and spec.loader:
                    m = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(m)
            except Exception as e:
                print(f"Error: Failed to load config file '{args.config}': {e}", file=sys.stderr)
                sys.exit(1)
        else:
            # モジュールとしてインポート試行
            module_name = args.config
            if module_name.endswith('.py'):
                module_name = module_name[:-3]
            
            # カレントディレクトリをパスに追加
            if os.getcwd() not in sys.path:
                sys.path.insert(0, os.getcwd())

            try:
                m = importlib.import_module(module_name)
            except ModuleNotFoundError:
                print(f"Error: Config module '{args.config}' not found.", file=sys.stderr)
                sys.exit(1)
        
        if m:
            if hasattr(m, 'data'):
                ope.datamap = m.data
            if hasattr(m, 'chr'):
                ope.cpu.strmap = m.chr
            if hasattr(m, 'output'):
                output = m.output

    for p in ope.datamap:
        if p[0] > p[1]:
            print(f"Error: Invalid data range in config: start=0x{p[0]:04X} > end=0x{p[1]:04X}", file=sys.stderr)
            sys.exit(1)

    # 逆アセンブル
    out = ope.exec(args.start, images, size)

    if(args.output is not None):
        stdout = sys.stdout
        with open(args.output, "w") as f:
            sys.stdout = f
            output(out, args.nodump)
            sys.stdout = stdout

    else:
        output(out, args.nodump)


def output_default(dis, sw):
    """逆アセンブラのデフォルト出力関数。

    Args:
        dis (list): 逆アセンブルデータリスト。
        sw (bool): ダンプなしフラグ (True: アドレスとオペコードを隠す)。
    """
    lbsize = 5
    # ラベル表示用に、最も長いラベルの長さを事前に計算しておく（オプション）
    # max_label_len = max((len(p.get("label", "")) for p in dis), default=0)

    for p in dis:
        if sw: # ダンプなし
            label = p.get("label", "")
            if label:
                print(label)
            if p.get("asm"):
                indent = "    " if p.get("opcode") else ""
                print(f'{indent}{p["asm"]}')
        else:
            addr_str = f'0x{p.get("address", 0):04X}'
            op_bytes = p.get("opcode", [])
            op_str = " ".join(f"{b:02X}" for b in op_bytes)
            label_str = p.get("label", "")
            asm_str = p.get("asm", "")
            # f-stringの桁揃え機能を使用: < は左寄せ、> は右寄せ
            print(f"{addr_str} {op_str:<12} {label_str:<{lbsize+1}} {asm_str}")


def main():
    # --------------------------------------------------------
    # main
    # --------------------------------------------------------
    parser = argparse.ArgumentParser(description=f"Z80 assembler & disassembler v{__about__.__version__}")
    subparsers = parser.add_subparsers()

    # disasm
    parser_disasm = subparsers.add_parser("disasm", help="Z80 disassembler")
    parser_disasm.add_argument("-i", "--input", nargs="*", required=True, help="input images")
    parser_disasm.add_argument("-c", "--config", help="disasm config file")
    parser_disasm.add_argument("-s", "--start", type=lambda x: int(x, 0), default=0, help="start address")
    parser_disasm.add_argument("-n", "--nodump", help="remove dump info", action="store_true")
    parser_disasm.add_argument("-o", "--output", help="output file")
    parser_disasm.set_defaults(handler=command_disasm)

    # asm
    parser_asm = subparsers.add_parser("asm", help="Z80 assembler")
    parser_asm.add_argument("-f", "--file", required=True, help="asm file")
    parser_asm.add_argument("-o", "--output", required=True, help="output file(bin)")
    parser_asm.add_argument("-s", "--size", help="*option* : output file(bin) size")
    parser_asm.set_defaults(handler=command_asm)

    args = parser.parse_args()

    if(hasattr(args, "handler")):
        args.handler(args)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
