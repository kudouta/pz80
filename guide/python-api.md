# Python API

> pz80 を**Python のモジュールとして使う**ときのリファレンスです。コマンドは [cli.md](cli.md)、アセンブリの構文は [language.md](language.md) を参照してください。

前半で用途ごとの使い方を、後半の「[リファレンス](#リファレンス)」で関数・クラスの一覧と戻り値の形式を説明します。

## 例を動かす準備

以降の例は `rom.bin` や `main.asm` といった入力ファイルを使います。**この節を 1 回実行しておけば、後の例はそのまま貼って動きます。** 空のディレクトリで実行してください。

```python
import pathlib

# 練習用の ROM（0x0123 に 'A' '@' … 'E' '$' を仕込んである）
rom = bytearray(0x1000)
rom[0x0000:0x0003] = bytes([0x3E, 0x2A, 0xC9])      # ld a, 0x2A / ret
rom[0x0123:0x0129] = b"A@pz80E$"[:6]
pathlib.Path("rom.bin").write_bytes(rom)
pathlib.Path("encrypted_rom.bin").write_bytes(bytes(b ^ 0x55 for b in rom))
pathlib.Path("font.bin").write_bytes(bytes(range(256)))

# 2KB ずつに分けた ROM チップ
for name, at in [("prg0.bin", 0x0000), ("prg1.bin", 0x0800),
                 ("prg2.bin", 0x1000), ("prg3.bin", 0x1800)]:
    pathlib.Path(name).write_bytes(bytes(rom[at % 0x1000:][:0x800]))

# アセンブリソース
pathlib.Path("main.asm").write_text("org 0x0000\nnop\nret\n", encoding="utf-8")
pathlib.Path("header.asm").write_text("HEADER: equ 0x1234\n", encoding="utf-8")
pathlib.Path("legacy.asm").write_text("ld a, $2A\nret\n", encoding="utf-8")
pathlib.Path("conditional.asm").write_text(
    "IF DEBUG\nnop\nELSE\nret\nENDIF\n", encoding="utf-8")
```

## アセンブル

```python
from pz80 import assemble

source_code = """
    ORG 0x100
    LD A, 42
    RET
"""
# ソースコードをアセンブルしてバイト列を取得
binary_data = assemble(source_code)
```

`assemble()` はソースの文字列のほか、後述の**チャンクのリスト**も受け付けます。どちらの場合も、置いた範囲の最小の番地から最大の番地までを返します。`ORG` で飛ばした範囲は `0x00` で埋まります。

```python
from pz80 import assemble

data = assemble("ORG 0x0000\nDB 0x11, 0x22\nORG 0x0008\nDB 0x33\n")
# b'\x11\x22\x00\x00\x00\x00\x00\x00\x33'  (9 バイト)
```

### Asm クラス

`Asm` クラスを直接使うと、行のリストやファイルからアセンブルでき、シンボル表も参照できます。

```python
from pz80 import Asm

# 行リストからアセンブル
lines_1 = ["ORG 0x100", "LD A, 42", "RET"]
lines_2 = ["ORG 0x200", "CALL 0x300", "HALT"]

result = Asm().assemble_lines(lines_1)

# file にソースを識別する文字列を渡しておくと、エラーメッセージが
# 「on line 3 in main_code」の形になり、どのソースの何行目か分かる
result = Asm().assemble_lines(lines_1, file="main_code")
result = Asm().assemble_lines(lines_2, file="sub_code")

# ファイル名指定で読み込み（file には "main.asm" が入る）
result = Asm().exec("main.asm")
```

`assemble_lines()` の呼び出しはそれぞれ独立していて、`ORG` もラベルも引き継がれません。上の例の `result` は最後の呼び出しの分だけです。**複数のソースを 1 つのバイナリにまとめるには `assemble_chunks()` を使ってください**（[後述](#複数チャンクの連結アセンブル)）。

`file` は**ソースを識別する任意の文字列**で、ファイルとして開かれることはありません。エラーメッセージに `in <file>` として出るほか、戻り値の各行の `"file"` キーに入ります。

```python
from pz80 import Asm

Asm().assemble_lines(["ld a, 300"])
# ValueError: Byte value 300 out of range on line 1 (expected -128 to 255)

Asm().assemble_lines(["ld a, 300"], file="main_code")
# ValueError: Byte value 300 out of range on line 1 in main_code (expected -128 to 255)
```

戻り値は行ごとの情報を持つリストです（形式は「[戻り値の形式](#戻り値の形式)」）。バイト列にするには `to_bytes()` に渡します。

```python
from pz80 import Asm, to_bytes

result = Asm().assemble_lines(["ld a, 0x2A", "ret"])
data = to_bytes(result)          # b'\x3e\x2a\xc9'
```

戻り値は**行の並び**で、メモリイメージではありません。`opcode` を単純につなぐと、`ORG` で飛ばした隙間が詰まってしまいます。

```python
# NG: ORG の隙間が失われる（上の 9 バイトの例なら 3 バイトになる）
data = [b for item in result if item.get("opcode") for b in item["opcode"]]
```

### 条件アセンブルのシンボル

`exec()` の `defines` は条件アセンブル用のシンボル `{名前: 値}` で、CLI の `-D` に当たります。ソースの先頭に `名前: EQU 値` を書いたのと同じ扱いです。

```python
from pz80 import Asm

Asm().exec("conditional.asm", defines={"DEBUG": 1})
Asm().exec("conditional.asm", defines={"DEBUG": "0x01"})   # 値は文字列でもよい
```

`IF` の条件に未定義のシンボルを書くとエラーになります（`0` とは扱われません）。構成を切り替えるソースでは、どの構成でも必ず値を渡してください。

### 複数チャンクの連結アセンブル

`assemble_chunks()` は `(識別子, 行のリスト)` のリストを受け取り、つないで 1 本のソースとしてアセンブルします。行のリストは Python で自由に作れるので、`INCLUDE`・マクロ・バイナリの埋め込み・他のアセンブラの記法の変換といった処理を Python で書けます。

```python
from pz80 import Asm

def include(filename):
    with open(filename, encoding="utf-8") as f:
        return f.readlines()

def djnz_loop(count, body):
    """Python関数によるマクロ風記述"""
    return [
        f"    LD B, {count}",
        "LOOP:",
        *body,
        "    DJNZ LOOP",
    ]

def embed_binary(filename):
    """バイナリファイルを DB 行のリストに変換する"""
    from pz80 import read_chunks
    return [f"    DB 0x{b:02X}" for b in read_chunks(filename)]

def convert_literals(lines):
    """他のアセンブラの数値表記を pz80 の形式へ読み替える"""
    import re
    return [re.sub(r"\$([0-9A-Fa-f]+)", r"0x\1", line) for line in lines]

chunks = [
    ("header.asm", include("header.asm")),  # 第1要素は第2要素（Python処理系）を識別するための文字列
                                            # 便宜上ファイル名と同じ文字列を使っているが、
                                            # ファイル名との依存関係はない。
    ("loop_macro", djnz_loop(10, ["NOP"])),
    ("bootcode",   include("main.asm")),
    ("legacy.asm", convert_literals(include("legacy.asm"))),
    ("font_data",  embed_binary("font.bin")),
]
result = Asm().assemble_chunks(chunks)
```

**チャンクは上から順につながり、1 本のソースとして扱われます。** 次のものはチャンクをまたいで引き継がれます。

* `ORG` で決めた番地
* ラベル（どのチャンクで定義しても、どのチャンクからも参照できる）
* `EQU` 定数（右辺の後方参照はチャンクをまたいでも有効。前方参照ができないのは 1 本のソースと同じ）
* `IF` / `ELSE` / `ENDIF` の入れ子（`IF` がチャンクをまたいでもよい）

行番号はチャンクごとに 1 から数えます。エラーメッセージには識別子が付くので、どのチャンクの何行目か分かります。

```
Byte value 256 out of range on line 7 in bootcode (expected -128 to 255)
```

* 同じ識別子が 2 回以上出てくると、2 つ目以降に `#2`, `#3` … が付きます（`in macros.asm#2`）。
* 識別子は文字列か `None` です。`None` と空文字列は「識別子なし」で、`on line 7` の形になります。
* 行のリストの代わりに 1 本の文字列を渡すとエラーになります。文字列から作るときは `splitlines()` で分けてください。

### アセンブル結果の一覧表示

一覧表示の機能はありませんが、戻り値の各行が `(識別子, 行番号)` を持つので、入力のチャンクを同じキーで引けば元のソース行（コメント込み）を添えた一覧が作れます。命令行の `asm` は `EQU` を置き換えた後の文字列なので、元のソース行を添えるとシンボル名も読めます。

```python
from pz80 import Asm

chunks = [
    ("struct.def", ["PLAYER: EQU 0xC000", "PLAYER.hp: EQU 0xC002", "DEBUG: EQU 1"]),
    ("main.asm",   include("main.asm")),
]
result = Asm().assemble_chunks(chunks)

# (識別子, 行番号) -> 元のソース行
source = {(n, i): ln.rstrip() for n, lines in chunks for i, ln in enumerate(lines, 1)}

for p in result:
    kind = p.get("kind", "label" if "label" in p else "")
    addr = f"{p['base'] + p['offset']:04X}" if "kind" not in p else "    "
    ops = " ".join(f"{b:02X}" for b in p.get("opcode", []))
    print(f"{addr}  {ops:<12s} {kind:<9s} {source[(p['file'], p['line'])]}")
```

```
ADDR  OPCODE       KIND      SOURCE
      equ                    PLAYER: EQU 0xC000
      equ                    PLAYER.hp: EQU 0xC002
      org                            ORG 0x8000
8000            label        START:
8000  21 02 C0                       LD HL,PLAYER.hp  ; メンバ
      if                             IF DEBUG
8003  00                             NOP
      else                           ELSE
      skipped                        HALT
      endif                          ENDIF
8004  C3 00 80                       JP START
```

`kind` を持つ行はバイトを生みません。**`"skipped"` は条件アセンブルで捨てられた行**で、`equ` / `org` / `if` / `else` / `endif`（疑似命令として消費された行）と区別できます。`-D` で条件を切り替えるとき、どちらの枝が使われたかを確かめられます。

## 逆アセンブル

```python
from pz80 import disassemble

# バイト列を逆アセンブルして命令リストを取得
binary_data = b'\x3E\x2A\xC9'
instructions = disassemble(binary_data, start_address=0x100)
for line in instructions:
    print(line)

# データ領域を指定して逆アセンブル（指定範囲は命令ではなく db になる）
instructions = disassemble(binary_data, start_address=0x100,
                           data_regions=[[0x0101, 0x0102]])

# 暗号化ROMをM1ハンドラーで復号しながら逆アセンブル
def decrypt(address, byte):
    return byte ^ 0x55

instructions = disassemble(binary_data, m1_handler=decrypt)

# エントリポイントにラベルを強制付与（NMI など参照のないアドレス用）
# walk() の extra_entries と同じリストを渡すと一貫したラベル付けになる
# 整数アドレスのほか、シンボル名 (NMI 等) も指定できる
instructions = disassemble(binary_data, start_address=0x0066,
                           label_addresses=["NMI"])

# キャラクターコード表を指定（データ領域の db コメント [文字] に反映）
from pz80 import Z80
chr_table = list(Z80().strmap)
chr_table[0xC7] = "@"          # 0xC7 を '@' として表示
instructions = disassemble(binary_data, data_regions=[[0x0000, 0x0002]],
                           strmap=tuple(chr_table))

# ラベルに名前を添える（定義側と参照側の両方が L_0004@DRAW_SPRITE になる）
# 名前を付けたアドレスを指す LD de, nn なども同じ綴りに置き換わる
jump_rom = b'\xC3\x04\x00\x00\x76'      # JP 0x0004 / NOP / HALT
instructions = disassemble(jump_rom, label_names={0x0004: "DRAW_SPRITE"})

# 逆アセンブル範囲外の定数（RAM・I/O）は equ_names で。裸の名前で出る
# 読み書きで役割が違うレジスタは {"r": ..., "w": ...} で分けられる
io_rom = b'\x3A\x00\xE0\x32\x00\xE0'    # LD a,(0xE000) / LD (0xE000),a
instructions = disassemble(io_rom,
                           equ_names={0xE000: {"r": "KeyIn", "w": "IntEnable"}})

# バイナリが実在する範囲を指定（bins の隙間を出力から除外する）
# walk() の valid_ranges と同じものを渡す。隙間の手前で org を出し直すので
# 出力はそのまま再アセンブルできる
gapped = b'\xC3\x08\x00\x76\x00\x00\x00\x00\x3E\x30\xC9\x00'
instructions = disassemble(gapped, valid_ranges=[[0x0000, 0x0003],
                                                 [0x0008, 0x000B]])
```

引数の意味は設定ファイルのキーと同じです（`data_regions` = `data`、`label_names` = `labels`、`equ_names` = `equ` など）。書き方の詳細は [config.md](config.md) を参照してください。

`disassemble()` は行の文字列だけを返します。`data` の `fmt` で範囲を割り切れないときの警告などを受け取るには、`Disasm` クラスを使い、`exec()` の後に `warnings` を読んでください。

### 行の無い番地を指す参照

`JP L_8000` のように、逆アセンブル結果に行が無い番地を指す参照には、先頭に `EQU` の定義が付きます。RAM へ飛ぶもの、命令の途中を指すもの、`valid_ranges` の隙間を指すものが該当します。

```asm
L_8000: EQU 0x8000
org 0x0000
    JP L_8000
    HALT
```

`equ_names` で同じ番地に名前を付けていても衝突しません。

## データ領域の検出 (walk)

```python
from pz80 import walk

with open("rom.bin", "rb") as f:
    binary = f.read()

# CFGトレースでデータ領域を検出
regions = walk(binary, start=0x0000, extra_entries=["NMI", "IM1"])
print(regions)
# → [[0x1000, 0x12FF], [0x2000, 0x2FFF]]
```

`extra_entries` にはシンボル名（`"NMI"`, `"IM1"` など）と整数を混ぜて書けます。使えるシンボル名は [cli.md の表](cli.md#エントリポイントのシンボル名)を参照してください。

```python
# disasm と組み合わせてデータ領域を正しく逆アセンブル
from pz80 import walk, Disasm

with open("rom.bin", "rb") as f:
    binary = f.read()

regions = walk(binary, start=0x0000, extra_entries=["NMI"])

d = Disasm()
d.datamap = regions
result = d.exec(0x0000, list(binary), len(binary))
```

### 複数のファイルを別々の番地に置く

`read_chunks()` と `valid_ranges` を組み合わせると、ファイルの間の隙間を除いて解析できます。

```python
import os
from pz80 import read_chunks, walk, Disasm

bins = [
    ("prg0.bin", 0x0000),
    ("prg1.bin", 0x0800),
    ("prg2.bin", 0x1000),
    ("prg3.bin", 0x3800),
]

# 各ファイルを指定アドレスに配置した images を取得
images = read_chunks(bins)

# valid_ranges: 各ファイルのアドレス範囲 (ファイルサイズから算出)
valid_ranges = [[addr, addr + os.path.getsize(path) - 1] for path, addr in bins]

# ギャップを除いたデータ領域を検出
regions = walk(images, start=0x0000, extra_entries=["NMI", "IM1"],
               valid_ranges=valid_ranges)

# 逆アセンブルにも適用。valid_ranges は disasm 側にも渡す
# （渡さないと隙間の 0x00 が nop の列として出て walk と見え方が食い違う）
d = Disasm()
d.datamap = regions
d.valid_ranges = valid_ranges
result = d.exec(0x0000, images, len(images))
```

隙間をはさむたびに `org` を出し直すので、出力はそのまま組み直せます。

## 暗号化 ROM の復号 (M1 ハンドラー)

`m1_handler` に復号の関数を渡します。`walk()` と `disassemble()` の両方に同じ関数を渡すと、データ領域の検出から逆アセンブルまで同じ復号が使われます。

```python
from pz80 import Disasm, read_chunks, walk, disassemble

data = read_chunks("encrypted_rom.bin")

def decrypt(address, byte):
    # アドレスとバイト値から復号キーを導出する例
    key = (address & 1) ^ ((byte & 0x80) >> 7)
    return byte ^ key

# エントリポイント（walk と disassemble で共通、シンボル名と整数の混在可）
entries = ["NMI", 0x0020]

# CFGトレースで暗号化ROMのデータ領域を検出
regions = walk(data, start=0x0000, extra_entries=entries, m1_handler=decrypt)

# 同じ entries をラベル付与にも流用できる（解決不要）
lines = disassemble(data, data_regions=regions, m1_handler=decrypt,
                    label_addresses=entries)
```

関数は `(address: int, byte: int) -> int` の形です。呼ばれるのは命令を読むとき（M1 サイクル）のバイトだけで、即値やディスプレースメントには呼ばれません。

| 命令の形 | 関数が呼ばれるバイト |
| --- | --- |
| 通常の命令 | 1 バイト目 |
| プレフィックス付き（`CB` / `DD` / `FD` / `ED` xx） | 1・2 バイト目 |
| `DDCB` / `FDCB`（`DD CB d op`） | 1・2 バイト目（3 バイト目のディスプレースメントと 4 バイト目のオペコードは対象外） |


**入力のデータは書き換えられません。** 復号は内部のコピーに対して行うので、同じ `data` を `walk()` と `disassemble()` に続けて渡しても、互いに影響しません。

## バイナリの読み書き

`read_chunks()` はバイナリファイルを整数のリストとして読みます。

```python
from pz80 import read_chunks

# 単一ファイル
data = read_chunks("rom.bin")  # → list[int]

# 複数ファイルをアドレス指定で配置
data = read_chunks([
    ("prg0.bin", 0x0000),
    ("prg1.bin", 0x0800),
    ("prg2.bin", 0x1000),
    ("prg3.bin", 0x3800),
])  # → list[int] (アドレス間のギャップは 0x00 で埋められる)
```

読んだリストは Python でそのまま調べたり加工したりできます。

```python
from pz80 import read_chunks

# パターン探索の例: 'A''@' で始まり 'E''$' で終わるブロックを検出
data = read_chunks("rom.bin")
A, AT = ord('A'), ord('@')
E, DOLLAR = ord('E'), ord('$')

for i in range(len(data) - 1):
    if data[i] == A and data[i + 1] == AT:
        for j in range(i + 2, len(data) - 1):
            if data[j] == E and data[j + 1] == DOLLAR:
                print(f"0x{i:04X} - 0x{j + 1:04X}")
                break
```

`write_chunks()` で書き戻せます。読む → 加工する → 書く、が Python の中で完結します。

```python
from pz80 import read_chunks, write_chunks

# ROM読み込み・加工・書き出し
data = read_chunks([("prg0.bin", 0x0000), ("prg1.bin", 0x0800)])
data[0x0123] = 0x00   # NOP に差し替え（パッチ）
write_chunks("patched.bin", data)

# 元のROM単位に分割して書き出し
write_chunks([
    ("prg0_patched.bin", 0x0000, 0x07FF),
    ("prg1_patched.bin", 0x0800, 0x0FFF),
], data)
```

## リファレンス

### 公開 API の一覧

| シンボル | 種別 | 内容 |
| --- | --- | --- |
| `assemble(source)` | 関数 | ソースの文字列かチャンクのリストをバイト列にする |
| `to_bytes(result)` | 関数 | アセンブル済みのリストをバイト列にする |
| `disassemble(data, start_address=0, data_regions=None, m1_handler=None, label_addresses=None, strmap=None, label_names=None, equ_names=None, valid_ranges=None, raw_operand=None, comments=None)` | 関数 | バイト列を逆アセンブルして、行の文字列のリストを返す |
| `read_chunks(source)` | 関数 | バイナリファイルを整数のリストとして読む |
| `write_chunks(dest, data)` | 関数 | 整数のリストをバイナリファイルに書く |
| `walk(data, start=0, extra_entries=None, valid_ranges=None, m1_handler=None)` | 関数 | 制御の流れを追ってデータ領域を返す |
| `Asm` | クラス | アセンブラ本体 |
| `Disasm` | クラス | 逆アセンブラ本体 |
| `Z80` | クラス | 命令表・予約語の参照 |
| `__version__` | 文字列 | pz80 のバージョン |

### Asm クラス

| メソッド | 内容 |
| --- | --- |
| `assemble_lines(lines, file=None)` | 行のリスト（`list[str]`）からアセンブル |
| `assemble_chunks(chunks)` | チャンクのリスト `[(識別子, 行のリスト), ...]` からアセンブル |
| `exec(name, defines=None)` | ソースファイルを読んでアセンブル |

`assemble_lines(lines, file=X)` は `assemble_chunks([(X, lines)])` と同じです。`exec(name)` では、渡したファイル名が `file` になります。`None` と空文字列はどちらも「識別子なし」です。

アセンブルの後に読める属性です。

| 属性 | 内容 |
| --- | --- |
| `labelmap` | シンボル表 `[{"type": "equ"\|"label", "symbol": str, "value": int}, ...]` |
| `label2address` | ラベルと番地の対応 `[{"label": str, "address": int}, ...]` |

* `labelmap` と `label2address` は同じシンボル表の別の見せ方で、食い違うことはありません。
* **`equ` は大文字・小文字を区別しません。** `symbol` は大文字にそろえて登録されます（`Val: EQU 5` は `VAL` になり、`LD A,val` からも参照できる）。
* **`label` は大文字・小文字を区別します。** `symbol` は書いたままで、`Start` と `START` は別のラベルです。

> `Asm` にはこのほか `symbols` / `cpu` / `encoder` / `preprocessor` / `directive_handler` という属性もありますが、内部の都合で持っているだけで API ではありません。予告なく変わります。

### 戻り値の形式

`assemble_lines()` / `assemble_chunks()` / `exec()` の戻り値は、ソースの並び順に次の 3 種類の辞書が入ったリストです。

| 種類 | 見分け方 | キー |
| --- | --- | --- |
| 命令行 | `"opcode"` を持つ | `line`, `file`, `asm`, `base`, `offset`, `opcode`, `fixups` |
| ラベル定義行 | `"label"` を持つ | `line`, `file`, `label`, `base`, `offset` |
| 消費された行 | `"kind"` を持つ | `line`, `file`, `asm`, `kind` |

* `kind` は `"equ"` / `"org"` / `"if"` / `"else"` / `"endif"` / `"skipped"` のどれかです。`"skipped"` は条件アセンブルで捨てられた行です。
* 各行の番地は `base + offset` です。消費された行は番地を持たないので、番地を求めるときは `"opcode"` か `"label"` を持つ行だけを使ってください。
* バイト列が欲しいだけなら、`assemble()` か `to_bytes()` を使ってください。

### Disasm クラス

| 名前 | 種別 | 内容 |
| --- | --- | --- |
| `exec(start, images, size)` | メソッド | バイナリイメージを逆アセンブルする |
| `op2asm(adr, opcode)` | メソッド | 1 命令のバイト列を文字列にする |
| `datamap` | プロパティ | データ領域（設定ファイルの `data`）。読むと `[[start, end], ...]` |
| `label_addresses` | 属性 | ラベルを付ける番地のリスト（設定ファイルの `entry`） |
| `label_names` | 属性 | `{番地: 名前}`（設定ファイルの `labels`） |
| `label_no_imm` | プロパティ | `label_names` で `{"imm": False}` にした番地の集合（読み取り専用） |
| `equ_names` | 属性 | `{番地: 名前 \| {"r": …, "w": …, "imm": …}}`（設定ファイルの `equ`） |
| `raw_operand` | プロパティ | オペランドを数値のまま出す命令の番地の集合 |
| `comments` | プロパティ | `{番地: 文字列 \| {"line": …, "block": …}}` |
| `valid_ranges` | プロパティ | バイナリが実在する範囲 `[[start, end], ...]`。隙間は出力から除外される |
| `m1_handler` | 属性 | 復号の関数 `(addr, byte) -> byte` |
| `cpu.strmap` | 属性 | `db` 行の `; [文字]` に使う 256 要素のタプル（設定ファイルの `chr`） |
| `warnings` | 属性 | 直前の `exec()` で出た警告の文字列のリスト。`exec()` ごとに作り直される |

`cpu` は `Disasm` が持つ `Z80` のインスタンスです。上の表にないメンバは内部の実装なので、依存しないでください。

### Z80 クラス

| 名前 | 種別 | 内容 |
| --- | --- | --- |
| `reserved` | プロパティ | 予約語（レジスタ・ニーモニック・疑似命令）のソート済みリスト |
| `asm_map` | プロパティ | アセンブラ用の表（ニーモニック → 命令の情報） |
| `op_map` | プロパティ | 逆アセンブラ用の表（バイト列 → 命令の情報） |
| `codetbl` | プロパティ | 命令表そのもの（1138 件のリスト）。`asm_map` / `op_map` の元 |
| `strmap` | プロパティ | バイト値 → 表示文字の 256 要素タプル |
