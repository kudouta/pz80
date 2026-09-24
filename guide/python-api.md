# Pythonモジュールとしての使用

> pz80 の**Python API リファレンス**です。CLI の使い方は [README.md](../README.md)、アセンブリ言語の仕様は [language.md](language.md) を参照してください。

Pythonのソースから pz80 をインポートして使用する例です。

## 公開API 一覧

| シンボル                                                                                                        | 種別  | 概要                    |
| ----------------------------------------------------------------------------------------------------------- | --- | --------------------- |
| `assemble(source)`                                                                                          | 関数  | アセンブリソース文字列またはチャンクリストをバイト列に変換 |
| `to_bytes(result)`                                                                                          | 関数  | アセンブル済みリストをバイト列に変換    |
| `disassemble(data, start_address=0, data_regions=None, m1_handler=None, label_addresses=None, strmap=None, label_names=None, equ_names=None, valid_ranges=None)` | 関数  | バイト列をアセンブリ文字列リストに変換   |
| `read_chunks(source)`                                                                                       | 関数  | バイナリファイルを整数リストとして読み込む |
| `write_chunks(dest, data)`                                                                                  | 関数  | 整数リストをバイナリファイルに書き出す   |
| `walk(data, start=0, extra_entries=None, valid_ranges=None, m1_handler=None)`                               | 関数  | 制御フローグラフでデータ領域を検出     |
| `Asm`                                                                                                       | クラス | アセンブラ本体 (詳細制御用)       |
| `Disasm`                                                                                                    | クラス | 逆アセンブラ本体 (詳細制御用)      |
| `Z80`                                                                                                       | クラス | Z80命令テーブル・予約語の参照      |
| `__version__`                                                                                               | 文字列 | pz80 のバージョン           |

### Asm クラスの主なメソッド

| メソッド                               | 概要                                  |
| ---------------------------------- | ----------------------------------- |
| `assemble_lines(lines, file=None)` | 行リスト (`list[str]`) からアセンブル          |
| `assemble_chunks(chunks)`          | 複数チャンク `[(識別子, 行リスト), ...]` からアセンブル |
| `exec(name, defines=None)`         | ソースファイルを読み込んでアセンブル                  |

`assemble_lines()` の `file` は**ソースを識別する任意の文字列**です。ファイルパスとして解釈されることはなく、ファイルシステムにアクセスもしません。用途は 2 つあります。

* エラーメッセージに `in <file>` として出る（省略時は `on line 1` の形）
* 戻り値の各要素の `"file"` キーに入る（リスティングを組むときの引き当てキー）

```python
from pz80 import Asm

Asm().assemble_lines(["ld a, 300"])
# ValueError: Byte value 300 out of range on line 1 (expected -128 to 255)

Asm().assemble_lines(["ld a, 300"], file="main_code")
# ValueError: Byte value 300 out of range on line 1 in main_code (expected -128 to 255)
```

`None` と空文字列はどちらも「識別子なし」として扱われます。`assemble_lines(lines, file=X)` は `assemble_chunks([(X, lines)])` と等価で、`exec(name)` は渡したファイル名が自動的に `file` になります。

`exec()` の `defines` は**条件アセンブル用のシンボル定義** `{名前: 値}` で、CLI の `-D` に対応します。ソース先頭に `名前: EQU 値` を前置したのと同じ扱いになるため、`IF` から参照でき、`labelmap` にも `equ` として現れます。

```python
from pz80 import Asm

Asm().exec("conditional.asm", defines={"DEBUG": 1})
Asm().exec("conditional.asm", defines={"DEBUG": "0x01"})   # 値は文字列でもよい
```

`IF` の条件に未定義のシンボルを書くとエラーになります（`0` とは扱われません）。バリアントを切り替えるソースでは、どの構成でも必ず定義を渡してください。

### Asm クラスの主な属性

アセンブル実行後に参照します。

| 属性               | 概要                                                                       |
| ---------------- | ------------------------------------------------------------------------ |
| `labelmap`       | シンボル表 `[{"type": "equ"\|"label", "symbol": str, "value": int}, ...]`     |
| `label2address`  | ラベルと確定アドレスの対応 `[{"label": str, "address": int}, ...]`                     |

`value` は `equ` / `label` とも整数です。ラベルのアドレスは Pass 2 で確定するため、アセンブル完了後に参照してください。

**`symbol` の大小文字の扱いは `type` によって異なります。**

* `equ` — **大小文字を区別しません。** `symbol` は大文字に正規化されます（`Val: EQU 5` を `VAL` として登録し、`LD A,val` からも参照できる）。ニーモニックが大小文字を区別しないことに合わせています。
* `label` — **大小文字を区別します。** `symbol` はソースに書かれたままで、`Start` と `START` は別のラベルになります。

`labelmap` と `label2address` は同じシンボル表を別の形で見せたものです。実体は 1 つなので、両者が食い違うことはありません。

> `Asm` にはこのほか `symbols` / `cpu` / `encoder` / `preprocessor` / `directive_handler` という属性もありますが、**内部実装の都合で持っているだけ**で API ではありません。予告なく変わるため依存しないでください。

### 戻り値の形式

`assemble_lines()` / `assemble_chunks()` / `exec()` の戻り値はアセンブル済みリストで、ソース上の並び順に次の3種類の辞書が入ります。

| 種類     | 判別方法           | キー                                                          |
| ------ | -------------- | ----------------------------------------------------------- |
| 命令行    | `"opcode"` を持つ | `line`, `file`, `asm`, `base`, `offset`, `opcode`, `fixups`  |
| ラベル定義行 | `"label"` を持つ  | `line`, `file`, `label`, `base`, `offset`                    |
| 消費された行 | `"kind"` を持つ   | `line`, `file`, `asm`, `kind`                                |

`kind` は `"equ"` / `"org"` / `"if"` / `"else"` / `"endif"` / `"skipped"` のいずれかです。**`"skipped"` は条件アセンブルで偽と判定されて捨てられた行**で、疑似命令として消費された行と区別できます。

消費された行は番地を占有しないため `base` / `offset` を持ちません。アドレスを求めるときは `"opcode"` か `"label"` を持つ要素だけを対象にしてください。

戻り値は**行の並び**であってメモリイメージではありません。バイト列が欲しいだけなら `assemble()` か `to_bytes()` を使ってください（「[アセンブル](#アセンブル)」を参照）。行ごとの情報を使ってリスティングを組む例は「[アセンブル結果の一覧表示](#アセンブル結果の一覧表示)」にあります。

### Disasm クラスの主なメソッド・属性

| 名前                          | 種別    | 概要                                             |
| --------------------------- | ----- | ---------------------------------------------- |
| `exec(start, images, size)` | メソッド  | バイナリイメージを逆アセンブル                                |
| `op2asm(adr, opcode)`       | メソッド  | 1命令のオペコードを文字列化                                 |
| `m1_handler`                | 属性    | M1サイクル復号ハンドラー `(addr, byte) -> byte`（暗号化ROM対応） |
| `label_addresses`           | 属性    | 強制的にラベルを付与するアドレスのリスト（NMI 等の参照なしエントリ用）          |
| `label_names`               | 属性    | `{アドレス: 名前}`。ラベルが `L_0066@NMI` の形になり、そこを指す 16 ビットオペランドも置き換わる |
| `equ_names`                 | 属性    | `{アドレス: 名前 \| {"r": …, "w": …}}`。範囲外の定数に `EQU` で名前を付ける |
| `datamap`                   | プロパティ | データ領域 `[[start, end], ...]` の設定                |
| `valid_ranges`              | プロパティ | バイナリが実在する範囲 `[[start, end], ...]`。隙間を出力から除外する  |
| `cpu.strmap`                | 属性    | バイト値 → 表示文字の256要素タプル                           |

`cpu` は `Disasm` が保持する `Z80` インスタンスです。上記以外のメンバは内部実装なので依存しないでください。

### Z80 クラスの主な属性

| 名前         | 種別    | 概要                                          |
| ---------- | ----- | ------------------------------------------- |
| `reserved` | プロパティ | 予約語 (レジスタ・ニーモニック・疑似命令) のソート済みリスト            |
| `asm_map`  | プロパティ | アセンブラ用マップ (ニーモニック → 命令情報)                   |
| `op_map`   | プロパティ | 逆アセンブラ用マップ (オペコード → 命令情報)                    |
| `codetbl`  | プロパティ | 命令表そのもの (1138 件のリスト)。`asm_map` / `op_map` の元 |
| `strmap`   | プロパティ | バイト値 → 表示文字の256要素タプル (逆アセンブル時のダンプに使う)        |

### walk のエントリポイント・シンボル名

`walk()` の `extra_entries` には以下のシンボル名 (文字列) または整数アドレスを指定できます。

| シンボル             | アドレス     | シンボル           | アドレス     |
| ---------------- | -------- | -------------- | -------- |
| `RESET` / `RST0` | `0x0000` | `RST4`         | `0x0020` |
| `RST1`           | `0x0008` | `RST5`         | `0x0028` |
| `RST2`           | `0x0010` | `RST6`         | `0x0030` |
| `RST3`           | `0x0018` | `RST7` / `IM1` | `0x0038` |
|                  |          | `NMI`          | `0x0066` |

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

## バイナリファイルの読み込み

`read_chunks()` はバイナリファイルを整数リストとして読み込む汎用ユーティリティです。

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

読み込んだリストはPythonで直接解析・加工できます。

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

`write_chunks()` と組み合わせると read → 加工 → write のワークフローが完結します。

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

`assemble()` はソース文字列のほか、後述する**チャンクリスト**も受け付けます。どちらの場合も配置先の最小アドレスから最大アドレスまでを返し、`ORG` で飛ばした範囲は `0x00` で埋まります。

```python
from pz80 import assemble

data = assemble("ORG 0x0000\nDB 0x11, 0x22\nORG 0x0008\nDB 0x33\n")
# b'\x11\x22\x00\x00\x00\x00\x00\x00\x33'  (9 バイト)
```

### Asm クラスを使った詳細制御

`Asm` クラスを直接使うと、行リストや複数チャンクからのアセンブルが可能です。

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

`assemble_lines()` の呼び出しはそれぞれ独立していて、状態は持ち越されません（`ORG` もラベルも引き継がれない）。上の例のように 2 本のソースを扱う場合、`result` は最後の呼び出しの分だけになります。**両方を 1 つのバイナリにまとめたいなら `assemble_chunks()` を使ってください**（後述）。`file` はそのときのチャンク識別子と同じ役割です。

戻り値は行ごとの情報を持つリストです（形式は「[戻り値の形式](#戻り値の形式)」を参照）。バイト列にするには `to_bytes()` に渡します。

```python
from pz80 import Asm, to_bytes

result = Asm().assemble_lines(["ld a, 0x2A", "ret"])
data = to_bytes(result)          # b'\x3e\x2a\xc9'
```

戻り値は**行の並び**であってメモリイメージではありません。各行の配置先は `base + offset` で、`ORG` で飛ばした範囲は行として存在しないため、`opcode` を単純に連結すると隙間が詰まります。

```python
# NG: ORG の隙間が失われる（上の 9 バイトの例なら 3 バイトになる）
data = [b for item in result if item.get("opcode") for b in item["opcode"]]
```

### 複数チャンクの連結アセンブル

`assemble_chunks()` は `(文字列, Python処理系)` のタプルリストを受け取り、すべてを連結してアセンブルします。Python 上で実装した関数をアセンブル時に動作させることでアセンブルの拡張機能をPythonで実現できます。

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

チャンクは**行のリスト**なので、`convert_literals` のように**前段で変換を挟む**こともできます。`INCLUDE` やマクロと同じく「アセンブラの文法を増やさず、Python 側で処理する」形になります。どう読み替えるかはソースごとに違うため、その判断をアセンブラに埋め込まずに済みます。

各チャンクに含まれる第1要素の文字列はエラーメッセージ出力時に表示するため、各チャンクを連結後でもエラー箇所を特定できます。

```
Byte value 256 out of range on line 7 in bootcode (expected -128 to 255)
```

**連結のセマンティクス:**

チャンクはリストの順に**上から連結**されます。独立した翻訳単位ではなく、1 本のソースとして扱われるため、次の状態がチャンクを跨いで継続します。`INCLUDE` の代替として設計しているため、この「テキストを貼り付ける」意味づけになります。

* `ORG` で設定したベースアドレス
* ラベル（どのチャンクで定義しても、どのチャンクからも参照できる）
* `EQU` 定数（EQU 右辺での後方参照はチャンクを跨いでも有効。前方参照が不可なのは 1 本のソースの場合と同じ）
* `IF` / `ELSE` / `ENDIF` のネスト状態（`IF` がチャンクを跨いでもよい）

行番号は**チャンクごとに 1 から**振られます。そのためエラー位置の特定には識別子が必要です。識別子が重複した場合は 2 つ目以降に `#2`, `#3` … が付いて一意になります（同じファイルを 2 回取り込む使い方を妨げないため）。

```
Byte value 256 out of range on line 7 in macros.asm#2 (expected -128 to 255)
```

識別子は文字列か `None` のみ受け付けます。`None` と空文字列は「識別子なし」として扱われ、`line 7` の形になります。

第2要素は行の並び（リストやタプル）である必要があり、1 本の文字列を渡すとエラーになります（1 文字ずつイテレートされてしまうため。文字列から作る場合は `splitlines()` で分割してください）。

### アセンブル結果の一覧表示

アセンブル結果を一覧表示する専用の機能はありません。ただし各行が `(識別子, 行番号)` を持つので、**入力チャンクを同じキーで引けるようにしておく**と、元のソース行（コメント込み）を添えたリスティングが作れます。

チャンクを Python 側で組み立てる使い方では、渡したソースが実際にどう解釈されたかを確かめる手段としてこれが効きます。命令行の `asm` は `EQU` 置換**後**なのでシンボル名が失われますが、元のソース行を添えれば残ります。

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

`kind` を持つ行はバイトを生成しません。**`"skipped"` だけが条件アセンブルで偽と判定されて捨てられた行**で、`equ` / `org` / `if` / `else` / `endif`（疑似命令として消費された行）と区別できます。`-D` で条件を切り替えるビルドで、どちらの枝が生きたかを確かめられます。

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
# アドレスは名前に残る。walk と --auto-entry がラベル名からアドレスを読み戻すため
# 名前を付けたアドレスを指す LD de, nn なども同じ綴りに置き換わる
jump_rom = b'\xC3\x04\x00\x00\x76'      # JP 0x0004 / NOP / HALT
instructions = disassemble(jump_rom, label_names={0x0004: "DRAW_SPRITE"})

# 逆アセンブル範囲外の定数（RAM・I/O）は equ_names で。裸の名前で出る
# 読み書きで役割が違うレジスタは {"r": ..., "w": ...} で分けられる
io_rom = b'\x3A\x00\xB0\x32\x00\xB0'    # LD a,(0xB000) / LD (0xB000),a
instructions = disassemble(io_rom,
                           equ_names={0xB000: {"r": "IrqEnable", "w": "NmiOn"}})

# バイナリが実在する範囲を指定（bins の隙間を出力から除外する）
# walk() の valid_ranges と同じものを渡す。隙間の手前で org を出し直すので
# 出力はそのまま再アセンブルできる
gapped = b'\xC3\x08\x00\x76\x00\x00\x00\x00\x3E\x30\xC9\x00'
instructions = disassemble(gapped, valid_ranges=[[0x0000, 0x0003],
                                                 [0x0008, 0x000B]])
```

### 行の無い番地を指す参照

`JP L_8000` のように**逆アセンブル結果に行が無い番地**を指す参照には、先頭に `EQU` の定義が付きます。RAM へ飛ぶもの、命令の途中を指すもの、`valid_ranges` の隙間を指すものが該当します。

```asm
L_8000: EQU 0x8000
org 0x0000
    JP L_8000
    HALT
```

定義が無いと `Undefined symbol` で再アセンブルできません。値は分かっているので `EQU` にしています。`equ_names` で同じ番地に名前を付けていても衝突しません（pz80 は値の同じ `EQU` を別名で定義できます）。

## データ領域検出 (walk)

```python
from pz80 import walk

with open("rom.bin", "rb") as f:
    binary = f.read()

# CFGトレースでデータ領域を検出
regions = walk(binary, start=0x0000, extra_entries=["NMI", "IM1"])
print(regions)
# → [[0x1000, 0x12FF], [0x2000, 0x2FFF]]
```

`extra_entries` にはシンボル名（`"NMI"`, `"IM1"` など）と整数アドレスを混在して指定できます。

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

### 複数バイナリファイルを異なるアドレスに配置する場合

`read_chunks()` と `valid_ranges` を組み合わせることで、ファイル間のギャップ領域を除外した正確な解析ができます。

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

隙間を挟むたびに `org` を出し直すので、出力はそのまま再アセンブルできます。命令の復号も区間の終端で止まるため、隙間のバイトを巻き込んだ命令ができることもありません。

## 暗号化バイナリーファイルの逆アセンブル (M1ハンドラー)

`m1_handler` を暗号化バイナリーファイルの復号化処理に利用できます（暗号化のロジックによる）。

設定した関数はオペコードバイト（プレフィックス含む）のフェッチ時のみ呼ばれ、即値やディスプレースメントなどのオペランドバイトには呼ばれません。

`walk()` と `disassemble()` の両方に `m1_handler` を渡すことで、データ領域検出から逆アセンブルまで一貫して復号できます。`label_addresses` に `walk()` と同じエントリポイントを渡せば、NMI など参照のないアドレスにもラベルが付き一貫します。

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

M1ハンドラーは `(address: int, byte: int) -> int` の形式で定義します。Z80のM1フェッチ規則に従い、以下のバイトにのみ適用されます：

| 命令パターン                     | M1対象バイト                                  |
| -------------------------- | ---------------------------------------- |
| 通常命令                       | バイト0のみ                                   |
| プレフィックス命令 (CB/DD/FD/ED xx) | バイト0・1                                   |
| DDCB/FDCB命令 (DD CB d op)   | バイト0・1のみ（バイト2のディスプレースメント・バイト3のオペコードは対象外） |


> **補足: 元データは破壊されません**
> `m1_handler` の戻り値（復号結果）は逆アセンブル出力にのみ反映され、入力した `data`（`read_chunks()` で構築した images など）は書き換えられません。内部で常にコピーに対して復号を適用するため、同じ `data` を `walk()` と `disassemble()` に続けて渡しても、一方の M1 復号が他方に影響することはありません。

