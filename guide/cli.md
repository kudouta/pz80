# CLI の使い方

> pz80 の**コマンドライン リファレンス**です。アセンブリ言語の仕様は [language.md](language.md)、Python API は [python-api.md](python-api.md)、概要は [README.md](../README.md) を参照してください。


`pz80` コマンド（または `python -m pz80`）で使用します。

> **本ページのヘルプ出力は Python 3.10 / 端末幅 90 桁で生成しています。**
> `argparse` の出力形式は Python のバージョンで変わるため（3.13 以降は
> `-f FILE, --file FILE` が `-f, --file FILE` と短縮されます）、
> お使いの環境では表示が異なる場合があります。

```bash
C:\>pz80
usage: pz80 [-h] {disasm,walk,asm} ...

Z80 assembler & disassembler v0.4.59

positional arguments:
  {disasm,walk,asm}
    disasm           Z80 disassembler
    walk             Detect data regions in binary via CFG tracing
    asm              Z80 assembler

options:
  -h, --help         show this help message and exit

C:\>
```

## アセンブラ (asm)

```bash
C:\>pz80 asm --help
usage: pz80 asm [-h] -f FILE -o OUTPUT [-s SIZE] [-D DEFINE]

options:
  -h, --help            show this help message and exit
  -f FILE, --file FILE  asm file
  -o OUTPUT, --output OUTPUT
                        output file(bin)
  -s SIZE, --size SIZE  *option* : output file(bin) size
  -D DEFINE, --define DEFINE
                        symbol definition for conditional assembly (repeatable, merged
                        left to right). NAME=VALUE, NAME (=1), or a Python dict.
                        example: -D DEBUG=1

C:\>
```

ソースファイルをアセンブルしてバイナリを出力します。

```bash
pz80 asm -f source.asm -o output.bin
```

**オプション:**

* `-f`, `--file`: 入力アセンブリファイル（必須）
* `-o`, `--output`: 出力バイナリファイル（必須）
* `-s`, `--size`: 出力ファイルサイズを指定（オプション）。指定サイズまで `0x00` でパディングします。
* `-D`, `--define`: 条件アセンブル用のシンボル定義（複数指定可）。`NAME=VALUE` または Python の辞書リテラルで渡します（後述）。

## 逆アセンブラ (disasm)

```bash
C:\>pz80 disasm --help
usage: pz80 disasm [-h] [-i INPUT] [-c CONFIG] [-s START] [-n] [-o OUTPUT]

options:
  -h, --help            show this help message and exit
  -i INPUT, --input INPUT
                        input image file (specify -i multiple times for multiple files)
  -c CONFIG, --config CONFIG
                        config file (Python module, see README)
  -s START, --start START
                        start address
  -n, --nodump          remove dump info
  -o OUTPUT, --output OUTPUT
                        output file

C:\>
```

バイナリファイルを0番地から順に配置して逆アセンブルします。

```bash
pz80 disasm -i prg0.bin -i prg1.bin -i prg2.bin
```

**オプション:**

* `-i`, `--input`: 入力バイナリファイル（複数指定可）。`-c` で `bins` を指定した場合は省略可。
* `-s`, `--start`: 逆アセンブル開始アドレス（デフォルト: `0x0000`、または `-c` の `start` 値）
* `-o`, `--output`: 出力ファイル（デフォルト: 標準出力）
* `-n`, `--nodump`: ダンプ情報を非表示にし、アセンブリのみ出力
* `-c`, `--config`: 設定ファイル（Python モジュール）（オプション）

### 設定ファイルについて

`-c` オプションでPythonモジュール形式の設定ファイルを指定することで、作業対象のバイナリファイルの配置・逆アセンブルの挙動をカスタマイズできます。ファイルパスまたはモジュール名のどちらでも指定できます。設定項目はすべてオプションで、定義した項目のみが適用されます。

| 変数名          | 型        | 対象            | 説明                                                                                    |
| ------------ | -------- | ------------- | ------------------------------------------------------------------------------------- |
| `bins`       | list     | disasm / walk | バイナリファイル配置リスト `[(ファイルパス, ロードアドレス), ...]`。指定時は `-i` 不要。                                |
| `start`      | int      | disasm / walk | 逆アセンブル/ウォーク開始アドレス。CLI の `-s` が未指定の場合に使用。                                              |
| `data`       | list     | disasm        | `db` として扱うアドレス範囲 `[[開始, 終了], ...]`（両端含む）。                                             |
| `chr`        | tuple    | disasm        | バイト値→表示文字の256要素タプル。未指定時は標準ASCIIテーブル（0x20〜0x7E）を使用。                                    |
| `output`     | function | disasm        | カスタム出力関数。未指定時は `アドレス オペコード ラベル ニーモニック` 形式で標準出力。                                       |
| `entry`      | list     | walk / disasm | 追加エントリポイント。シンボル名または整数アドレスで指定。walk では CLI の `-e` とマージ、disasm ではラベル付与（`L_xxxx:`）に流用される。 |
| `labels`     | dict     | disasm        | `{アドレス: 名前}`。ラベルが `L_0066@NMI` の形になる。キーはシンボル名も可。逆アセンブル範囲内のみ。                         |
| `equ`        | dict     | disasm        | `{アドレス: 名前}`。範囲外の定数（RAM・I/O）に `EQU` で名前を付ける。`{"r": …, "w": …, "imm": …}` で読み・書き・即値を分けられる。   |
| `m1_handler` | function | disasm / walk | M1サイクル復号ハンドラー `(address, byte) -> byte`。暗号化ROM対応。                                     |

各属性の詳細な使用例は「[設定ファイル詳細](#設定ファイル詳細)」を参照してください。

## ウォーカー (walk)

```bash
C:\>pz80 walk --help
usage: pz80 walk [-h] [-i INPUT] [-c CONFIG] [-s START] [-e ADDR_OR_SYMBOL]
                 [--auto-entry]

options:
  -h, --help            show this help message and exit
  -i INPUT, --input INPUT
                        input binary file (specify -i multiple times for multiple files)
  -c CONFIG, --config CONFIG
                        config file (Python module, see README)
  -s START, --start START
                        start address (default: 0x0000)
  -e ADDR_OR_SYMBOL, --entry ADDR_OR_SYMBOL
                        additional entry point (address or: RESET/RST0-7/IM1/NMI)
  --auto-entry          auto-detect entry points from dispatch idioms (jump tables etc.)

C:\>
```

バイナリファイルを制御フローグラフで解析し、コードとして到達できないアドレス範囲をデータ領域として出力します。出力は `disasm` の設定ファイルの `data` 変数として直接利用できる形式です。

```bash
pz80 walk -i rom.bin -e NMI -e IM1
```

出力例:

```python
data = [
    [0x1000, 0x12FF],
    [0x2000, 0x2FFF],
]
```

**オプション:**

* `-i`, `--input`: 入力バイナリファイル（複数指定可）。複数ファイルは先頭から順に連結して扱います。`-c` で `bins` を指定した場合は省略可。
* `-c`, `--config`: 設定ファイル（Python モジュール）。バイナリファイル配置（`bins`）・エントリポイント（`entry`・`start`）を記述できます（オプション）。
* `-s`, `--start`: メインエントリポイント（デフォルト: `0x0000`、または `-c` の `start` 値）。CLI 指定が `-c` より優先。
* `-e`, `--entry`: 追加エントリポイント（複数指定可）。シンボル名または16進数アドレスで指定。`-c` の `entry` とマージされます。
* `--auto-entry`: ジャンプテーブル等からエントリポイントを自動抽出します（後述）。

### 設定ファイルを使ったバイナリファイル配置指定

複数のバイナリファイルを異なるアドレスに配置する場合は `-c` 設定ファイルの `bins` 属性を使います。ギャップ領域（バイナリファイル未配置のアドレス）は自動的に出力から除外されます。

```bash
pz80 walk -c rom_layout.py
```

```python
# rom_layout.py
bins = [
    ("prg0.bin", 0x0000),
    ("prg1.bin", 0x0800),
    ("prg2.bin", 0x1000),
    ("prg3.bin", 0x1800),
    ("prg4.bin", 0x2000),
    ("prg5.bin", 0x2800),
    ("prg6.bin", 0x3000),
    ("prg7.bin", 0x3800),
]
entry = ["NMI", "IM1"]  # 追加エントリポイント（-e と同等）
start = 0x0000          # メインエントリポイント（-s と同等、CLI が優先）
```

同じ設定ファイルを `disasm -c` に渡すと、`bins` を使って同じバイナリファイル配置で逆アセンブルできます。`data` や `output` など disasm 専用の属性も同じファイルにまとめて記述できます。

**ギャップの扱いは `walk` と `disasm` で揃っています。** どちらも出力から除外します。`disasm` はギャップを飛ばすたびに `org` を出し直すので、出力はそのまま再アセンブルできます。

### エントリポイントのシンボル名

Z80 の固定ベクタアドレスをシンボル名で指定できます。

| シンボル             | アドレス     | 説明                   |
| ---------------- | -------- | -------------------- |
| `RESET` / `RST0` | `0x0000` | リセットベクタ              |
| `RST1`           | `0x0008` | RST 1                |
| `RST2`           | `0x0010` | RST 2                |
| `RST3`           | `0x0018` | RST 3                |
| `RST4`           | `0x0020` | RST 4                |
| `RST5`           | `0x0028` | RST 5                |
| `RST6`           | `0x0030` | RST 6                |
| `RST7` / `IM1`   | `0x0038` | RST 7 / 割り込みモード1ハンドラ |
| `NMI`            | `0x0066` | 非マスカブル割り込みハンドラ       |

### disasm との連携例

```bash
# 1. ウォーカーでデータ領域を検出して設定ファイルに保存
pz80 walk -i rom.bin -e NMI > config.py

# 2. 生成した設定ファイルを使って逆アセンブル
pz80 disasm -i rom.bin -c config.py
```

### 追跡する分岐命令

制御フローグラフは以下の命令の分岐先を追跡します。

* `JP` / `JR` / `CALL` / `DJNZ`：直接アドレス指定の分岐・呼び出し。
* `RST nn`：固定ベクタ（`0x0000`〜`0x0038`）へのサブルーチン呼び出しとして分岐先を追跡しつつ、後続命令も継続します。

### エントリポイントの自動抽出 (`--auto-entry`)

`JP (HL)` のような間接分岐でトレースは停止します。分岐先はジャンプテーブル内の値なので、通常は `-e` で手動指定が必要です。`--auto-entry` は、**手書きアセンブラのディスパッチは書き方の定型句が有限個しかない**という前提に立ち、到達済みコードから定型句を認識してテーブル基底を逆算します。

```bash
pz80 walk -i rom.bin --auto-entry
```

```python
# auto-entry: [sp-ret] @0x00E6 task resume: return address depends on the runtime stack; cannot be resolved statically
# auto-entry: [jp-indirect] @0x0111 table=0x0112 stride=2 -> 0x1000 0x2000 0x3000 0x0A7C
# auto-entry: entry = -e 0x0000 -e 0x0038 -e 0x0066 -e 0x0A7C -e 0x1000 -e 0x2000 -e 0x3000
data = [
    [0x000B, 0x0037],
    [0x0072, 0x007F],
    [0x0112, 0x0141],
    [0x015D, 0x03FF],
    [0x0415, 0x0A7B],
    [0x0A97, 0x0FFF],
    [0x1023, 0x1FFF],
    [0x201E, 0x2FFF],
    [0x3026, 0x37FF],
]
```

**最後の `entry =` 行が結果**で、その上の行は**抽出根拠**です。`[sp-ret]` は「静的に解決できなかった」という報告なのでエントリを生みません。`[jp-indirect]` がテーブルから 4 つ抽出し、残りはベクタと `-e` 指定ぶんです。

`# auto-entry:` 行は Python コメントなので、出力をそのまま設定ファイルに保存できます。抽出根拠が残るため、内容を確認したうえで `-e` に固定する使い方を想定しています。認識する定型句は以下のとおりです。

| 定型句 | 扱い |
| --- | --- |
| `LD HL,tbl` → 添字加算 → `JP (HL)` / `JP (IX)` / `JP (IY)` | テーブルを読む |
| テーブル基底が RAM 経由（`LD (ram),HL`、バイト単位の分割書き込み） | 書き込み元を逆引き |
| `PUSH HL` + `RET`（戻り番地の偽装） | テーブルを読む |
| `CALL disp` 直後にテーブルを埋め込み、`disp` 側が `POP HL` で取得 | 戻り番地をテーブル基底とする |
| テーブル本体が `JP nnnn` の並び | 各スロット先頭をエントリにする |
| `RST n`（到達コード中に実在する場合のみ） | ベクタを採用 |
| `LD SP,HL` + `RET` / `RETN`（タスク再開） | 静的解決不可のため報告のみ |

> **注意**: 誤ったエントリはデータをコードとして誤認させます。`--auto-entry` は既定では無効で、出力される抽出根拠を確認したうえで使ってください。

### アルゴリズムの限界

* `JP (HL)` / `JP (IX)` / `JP (IY)` などの間接分岐は実行時の値が不明なため、分岐先を追跡できません。ジャンプテーブルやステートマシンで使われる場合、その先のコードを `-e` で手動指定するか、`--auto-entry` で抽出します。
* `LD SP,HL` + `RET` によるタスク再開など、復帰先が実行時のスタック内容に依存する分岐は静的に解決できません。`--auto-entry` は該当箇所を報告するのみです。
* IM2（割り込みモード2）のベクタテーブル経由の呼び出しは `-e` で手動指定が必要です。

### 追えなかった分岐先の報告

分岐先を復号できなかった場合、`# unresolved:` のコメント行で報告します。

```
# unresolved: 0x2000
# unresolved: branch targets above could not be decoded.
# unresolved: a ROM file may be missing from bins, or an entry is wrong.
data = [
    ...
```

**正常なら 1 行も出ません。** 手書きの Z80 コードは ROM の外へ分岐しないので、実 ROM では 0 件になります。**1 件でも出たら異常の合図**で、いちばん多い原因は `bins` に ROM ファイルを置き忘れたことです。上の例なら `0x2000` に置くはずのファイルが足りていません。

`# auto-entry:` と同じ Python のコメントなので、出力をそのまま設定ファイルへ保存できます。

# 設定ファイル詳細

`-c` で指定する設定ファイルはPythonソースで構成します。各属性について下記にまとめます。

## バイナリファイル配置 (`bins` / `start`)

複数のバイナリファイルをZ80アドレス空間の異なる位置に配置します。`disasm` と `walk` の両コマンドで使用されます。

```python
# rom_layout.py
bins = [
    ("prg0.bin", 0x0000),
    ("prg1.bin", 0x0800),
    ("prg2.bin", 0x1000),
    ("prg3.bin", 0x1800),
    ("prg4.bin", 0x2000),
    ("prg5.bin", 0x2800),
    ("prg6.bin", 0x3000),
    ("prg7.bin", 0x3800),
]

start = 0x0000  # 逆アセンブル/ウォーク開始アドレス（CLI の -s が優先）
```

```bash
pz80 disasm -c rom_layout.py
pz80 walk   -c rom_layout.py
```

### ファイル間のギャップ

どのファイルも置かれていないアドレスは、`disasm` でも `walk` でも**出力から除外**されます。実在しないバイトなので、命令としてもデータとしても出しません。

`disasm` はギャップを飛ばすたびに `org` を出し直します。

```asm
org 0x0000
    ...
org 0x0800
    ...
```

そのまま再アセンブルすれば元のアドレス配置に戻ります。命令の復号もファイルの終端で止まるため、ギャップのバイトを巻き込んだ命令ができることはありません。

ギャップのアドレスには `labels` を付けられません（ラベルを貼る行が無いため）。付けるとエラーになるので `equ` を使ってください。

## データ領域指定 (`data`)

指定したアドレス範囲を命令ではなくデータ（`db`）として出力します。`disasm` 専用。

```python
data = [
    [0x8000, 0x80FF],  # スプライトデータ
    [0x8100, 0x81FF],  # タイルマップ
]
```

出力形式：`db 0xXX ; [文字]`（`[文字]` は `chr` テーブルで決まる）

## 文字テーブル (`chr`)

`db` 行のコメント `; [文字]` に使われる256要素のタプルです。`disasm` 専用。

**デフォルトの動作:**

| アドレス範囲    | 表示                        |
| --------- | ------------------------- |
| 0x00〜0x1F | `.`（制御文字）                 |
| 0x20〜0x7E | ASCII印刷可能文字そのまま（スペース〜`~`） |
| 0x7F〜0xFF | `.`                       |

**カスタマイズ方法:**

全256要素を定義する必要があります。デフォルトテーブルを取得して一部変更するのが効率的です。

```python
from pz80 import Z80

# デフォルトテーブルをベースに一部変更
_chr = list(Z80().strmap)
_chr[0xC7] = "@"   # 0xC7 → '@'
_chr[0xF3] = "f"   # 0xF3 → 'f'
_chr[0xFB] = "h"   # 0xFB → 'h'
chr = tuple(_chr)
```

全256要素を定義すれば独自のコード体系にも対応できます。

```python
chr = (
    #  0    1    2    3    4    5    6    7    8    9    A    B    C    D    E    F
    "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "A", "B", "C", "D", "E", "F",  # 00
    "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V",  # 10
    "W", "X", "Y", "Z", ".", ".", ".", ".", ".", ".", ".", ".", ".", ".", ".", ".",  # 20
    # ... 以下 0xFF まで256要素
)
```

## カスタム出力 (`output`)

逆アセンブル結果の出力形式を完全に制御できます。`disasm` 専用。

```python
def output(dis, sw):
    """
    dis: 逆アセンブルデータのリスト
    sw:  --nodump フラグ (True: アドレス・オペコードを非表示)
    """
    for p in dis:
        if p.get("label"):
            print(p["label"])
        if p.get("asm"):
            indent = "    " if p.get("opcode") else ""
            print(f'{indent}{p["asm"]}')
```

`dis` の各要素の構成：

| キー        | 型          | 説明                            |
| --------- | ---------- | ----------------------------- |
| `address` | int        | 命令のアドレス                       |
| `opcode`  | list\[int] | オペコードのバイト列（ORG行などでは存在しない場合あり） |
| `asm`     | str        | アセンブリ文字列（例: `ld a, 0x10`）     |
| `label`   | str        | ラベル文字列（存在する場合のみ。例: `L_0100:`） |

## エントリポイント (`entry`)

`walk` コマンドの追加エントリポイントを config に記述できます。CLI の `-e` とマージされます。

```python
entry = ["NMI", "IM1", 0x0018]  # シンボル名・整数アドレスの混在可
```

`disasm` コマンドでは、同じ `entry` がラベル付与（`L_xxxx:`）に流用されます。NMI など逆アセンブル結果のコード中に参照のないエントリポイントにもラベルが付き、`walk` と一貫したラベル付けになります。

## ラベル名 (`labels`)

逆アセンブラが出すラベルに人間向けの名前を添えます。`disasm` 専用。

```python
labels = {
    "NMI":   "VBLANK",      # キーはベクタ名でも整数アドレスでもよい
    0x0100:  "DRAW_ROW",
    0x0120:  "MSG_TABLE",
    0x0140:  "StrA.D.1980",  # 英数字・`_`・`.` が使える
}
```

名前に使えるのは**英数字・`_`・`.`** です。`,` や `(` のようにトークンが切れてしまう文字を書くとエラーになります（そのまま通すと、出力は正しく見えるのに再アセンブルできない状態になるため）。

```
0x0100 11 20 01     L_0100@DRAW_ROW:   LD de, L_0120@MSG_TABLE
0x0103 CD 00 01                        CALL L_0100@DRAW_ROW
0x0106 C9                              RET
0x0120 07           L_0120@MSG_TABLE:  db 0x07 ; [.]
```

**定義側と参照側の両方に、同じ綴りで出ます。** 呼び出し箇所を見ただけで何を呼んでいるか分かるので、定義行まで戻る必要がなくなります。

名前を付けたアドレスには、**コード中に参照が無くてもラベルが付きます**。`LD de, 0x0120` のようにジャンプ以外から指されるデータの先頭に名前を付ける用途を想定しています。

**そのアドレスを指す 16 ビットオペランドもラベルに変わります。**

```asm
LD de, L_0120@MSG_TABLE       ; labels に 0x0120 があるので置き換わる
LD a,  (L_0120@MSG_TABLE)     ; 間接参照も同じ
LD ix, L_0120@MSG_TABLE       ; 4バイト命令も同じ

LD bc, 0x0200                 ; labels に無いので数値のまま
```

対象は分岐以外の `LD` 22 命令（即値・間接の両方）です。

> **置き換えるのは `labels` で名前を付けたアドレスだけです。**
>
> 16 ビット即値はアドレスとは限りません。`LD bc, 0x0100` はカウンタの初期値かもしれず、`LD hl, 0x4000` は VRAM のベースかもしれない。逆アセンブラには区別が付かないので、既定では触りません。名前を付けた時点で「ここは意味のある番地だ」と宣言したことになるため、そこだけ置き換えます。
>
> `entry` で付けたラベルは置き換え対象外です。名前が無いうえ、`entry` にありがちな `0x0000` を巻き込むと `LD hl, 0`（HL のクリア）まで `LD hl, L_0000` になってしまいます。

> **アドレスは名前に残ります**（`L_0066@NMI` であって `L_@NMI` ではありません）。
>
> `walk` と `--auto-entry` はラベル名からアドレスを読み戻しているので、アドレスを捨てると解析が成立しなくなります。区切りが `@` なのも同じ理由で、`_` や英数字を使うと `walk` 側の単語境界判定に掛かりません。
>
> なお**アセンブラはこの名前を解釈しません**。`L_0066@NMI` は不透明な識別子として扱われ、値は定義行の位置で決まります。出力に手を入れて番地がずれても、名前が古くなるだけでアセンブル結果は正しいままです。

## 定数名 (`equ`)

RAM・I/O・ハードウェアレジスタのように**逆アセンブル範囲の外**にあるアドレスへ名前を付けます。`disasm` 専用。

```python
equ = {
    0x8000: "MirrorRam",
    0xB000: {"r": "IrqEnable", "w": "NmiOn"},   # 読み書きで役割が違う
    0xB801: {"w": "SndVolume"},                 # 書き専用
}
```

読み書きを分ける必要がなければ、値は文字列 1 つで構いません。`dict(r=…, w=…)` と書いても同じものになりますが、**ruff の `C408`（`Unnecessary dict() call`）に引っかかる**ので、設定ファイルを lint にかけるなら波括弧の方が無難です。

```asm
MirrorRam: EQU 0x8000
IrqEnable: EQU 0xB000
NmiOn:     EQU 0xB000

    org 0x0000
    LD  hl, MirrorRam        ; 裸の名前（L_8000@ は付かない）
    LD  a, (IrqEnable)       ; 読みなので IrqEnable
    LD  (NmiOn), a           ; 書きなので NmiOn
```

**`labels` との使い分けは「貼る行があるか」です。** `labels` は逆アセンブル結果の行にラベルを付ける機構なので、範囲外のアドレスには定義を置く場所がありません。そちらに `labels` を書くとエラーになり、`equ` を使うよう促されます。

`equ` は住所を名前に残しません（`L_8000@MirrorRam` ではなく `MirrorRam`）。住所は `EQU` の定義行にあり、手書きでも同じ場所だからです。

読み書きの判定は命令の形で決まります。`LD a, (nn)` 系が読み、`LD (nn), a` 系が書き、`LD hl, nn` のような即値はどちらとも決まらないため `r` を先に見ます。片方しか名前が無ければ、もう一方でもその名前を使います。

### 即値だけ別の名前にする (`imm`)

読むと入力ポート・書くと出力ラッチ、という番地があります。この番地を `LD hl, nn` で扱うのは**出力ラッチをまとめて初期化するループの先頭**であることが多く、そこでは書き名が正しい名前です。既定は読み名を先に見るので外れます。

`imm` を書くと、即値のときだけその名前を使います。

```python
equ = {
    0xB000: {"r": "Dsw", "w": "NmiOn", "imm": "NmiOn"},
}
```

```asm
Dsw:   EQU 0xB000
NmiOn: EQU 0xB000

    org 0x0000
    LD  hl, NmiOn            ; 即値なので imm
    LD  a, (Dsw)             ; 読みは変わらない
    LD  (NmiOn), a           ; 書きも変わらない
```

名前を探す順は次のとおりです。

| 命令の形 | 順 |
| --- | --- |
| `LD a, (nn)` 読み | `r` → `w` → `imm` |
| `LD (nn), a` 書き | `w` → `r` → `imm` |
| `LD hl, nn` 即値 | `imm` → `r` → `w` |

向きが決まっている命令では `imm` を先に見ません。`imm` は「向きが分からないとき」の指定なので、分かっている命令の名前を上書きする意味がないためです。ただし最後の候補には置いてあるので、`{"imm": "X"}` だけ書けば 3 つの形すべてで `X` になります。

`EQU` の定義行は**違う名前の数だけ**出ます。上の例は `imm` が `w` と同じ名前なので 2 行です。`imm` を書かない設定では出力は従来と変わりません。

> 同じアドレスに `labels` と `equ` の両方があるときは、オペランドの置き換えは `equ` 側が優先されます。

### 自動で付く `EQU`

`JP L_8000` のように**逆アセンブル結果に行が無い番地**を指す参照には、`equ` を書かなくても定義が付きます。RAM へ飛ぶもの、命令の途中を指すもの、`bins` のギャップを指すものが該当します。

```asm
L_8000: EQU 0x8000
```

定義が無いと `Undefined symbol` で再アセンブルできないためです。読みやすい名前を付けたければ `equ` に書いてください（両方出ますが、値の同じ `EQU` を別名で定義できるので衝突しません）。

## M1ハンドラー (`m1_handler`)

Z80オペコード読み込み時に呼び出されるハンドラーで config に記述できます。`disasm` と `walk` の両方で使用されます。使い方はピンとこないかもしれませんが、例えば暗号化されたバイナリーファイルの復号タイミングに利用できます。

```python
def m1_handler(address, byte):
    # アドレスとバイト値から復号キーを導出する例
    key = (address & 1) ^ ((byte & 0x80) >> 7)
    return byte ^ key
```

config に `m1_handler` を定義することで、`walk` によるデータ領域検出と `disasm` による逆アセンブルの両方に同じ復号ロジックが適用されます。

## 設定ファイルの総合例

バイナリファイル配置・データ領域・文字テーブル・出力・M1ハンドラーをまとめた例です。`disasm` と `walk` の両方に使用できます。

```python
# rom_layout.py

# バイナリファイル配置（walk / disasm 共通）
bins = [
    ("prg0.bin", 0x0000),
    ("prg1.bin", 0x0800),
    ("prg2.bin", 0x1000),
    ("prg3.bin", 0x1800),
    ("prg4.bin", 0x2000),
    ("prg5.bin", 0x2800),
    ("prg6.bin", 0x3000),
    ("prg7.bin", 0x3800),
]
start = 0x0000

# walk 用エントリポイント
entry = ["NMI", "IM1"]

# M1サイクル復号ハンドラー（walk / disasm 共通）
def m1_handler(address, byte):
    key = (address & 1) ^ ((byte & 0x80) >> 7)
    return byte ^ key

# データ領域（disasm 用）
data = [
    [0x3900, 0x3FFF],
]

# 文字テーブル（disasm 用）: デフォルトをベースに一部変更
from pz80 import Z80
_chr = list(Z80().strmap)
_chr[0xC7] = "@"
chr = tuple(_chr)

# カスタム出力（disasm 用）
def output(dis, sw):
    for p in dis:
        if p.get("label"):
            print(p["label"])
        if p.get("asm"):
            indent = "    " if p.get("opcode") else ""
            print(f'{indent}{p["asm"]}')
```

