# CLI リファレンス

> `pz80` の**コマンドとオプションのリファレンス**です。`-c` で渡す設定ファイルは [config.md](config.md)、解析の進め方は [workflow.md](workflow.md)、アセンブリの構文は [language.md](language.md) を参照してください。

`pz80` コマンド（または `python -m pz80`）で使います。サブコマンドは 3 つです。

| サブコマンド | 内容 |
| --- | --- |
| [`asm`](#asm) | アセンブリソースをバイナリにする |
| [`disasm`](#disasm) | バイナリを逆アセンブルする |
| [`walk`](#walk) | 制御の流れを追って、コードとデータの境目を調べる |

> **このページのヘルプ出力は Python 3.10 / 端末幅 90 桁で生成しています。**
> `argparse` の表示は Python のバージョンで変わるため（3.13 以降は
> `-f FILE, --file FILE` が `-f, --file FILE` と短く出ます）、
> お使いの環境では表示が異なる場合があります。

```bash
C:\>pz80
usage: pz80 [-h] {disasm,walk,asm} ...

Z80 assembler & disassembler v0.4.64

positional arguments:
  {disasm,walk,asm}
    disasm           Z80 disassembler
    walk             Detect data regions in binary via CFG tracing
    asm              Z80 assembler

options:
  -h, --help         show this help message and exit

C:\>
```

## asm

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

アセンブリソースをアセンブルして、バイナリを出力します。

```bash
pz80 asm -f source.asm -o output.bin
```

| オプション | 内容 |
| --- | --- |
| `-f`, `--file` | 入力のアセンブリファイル（必須） |
| `-o`, `--output` | 出力のバイナリファイル（必須） |
| `-s`, `--size` | 出力の大きさ。足りない分は `0x00` で埋める |
| `-D`, `--define` | 条件アセンブル用のシンボル（何度でも指定可）。書き方は [language.md](language.md#条件アセンブル-if--elseif--else--endif) を参照 |

## disasm

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

バイナリを逆アセンブルします。

```bash
pz80 disasm -i rom.bin
pz80 disasm -i rom.bin -c rom_config.py -n -o rom.asm
```

| オプション | 内容 |
| --- | --- |
| `-i`, `--input` | 入力のバイナリ（複数指定可。0 番地から順に並べる）。設定ファイルに `bins` を書けば不要 |
| `-c`, `--config` | 設定ファイル（[config.md](config.md)） |
| `-s`, `--start` | 開始番地（既定は `0x0000`、または設定ファイルの `start`） |
| `-n`, `--nodump` | 番地とバイト列を出さず、アセンブリだけを出す |
| `-o`, `--output` | 出力ファイル（既定は標準出力）。UTF-8 で書く |

出力は 2 つの形があります。

```
0x0100 3E 01        L_0100:  LD a, 0x01        ← 既定（番地・バイト列付き）
L_0100:
    LD a, 0x01                                ← -n
```

**どちらの形も、そのまま `pz80 asm` で組み直せます。** 組み直した結果は元のバイナリと一致します（[workflow.md](workflow.md#5-組み直して確かめる)）。

設定ファイルとデータがかみ合わない箇所（`data` の `fmt` で範囲を割り切れない、など）は、`Warning:` で始まる行として標準エラーに出ます。

## walk

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

エントリポイントから制御の流れを追い、**コードとして到達できない範囲**を出力します。出力は設定ファイルの `data` の形なので、そのまま保存して `disasm -c` に渡せます。

```bash
pz80 walk -i practice.bin -e NMI > practice_cfg.py
pz80 disasm -i practice.bin -c practice_cfg.py
```

```python
data = [
    [0x0007, 0x0017],
    [0x0023, 0x0065],
    [0x0071, 0x018F],
    [0x01AA, 0x01BA],
    [0x01BC, 0x03FF],
]
```

（[workflow.md](workflow.md) の練習用 ROM での出力です）

| オプション | 内容 |
| --- | --- |
| `-i`, `--input` | 入力のバイナリ（複数指定可。先頭から順に連結する）。設定ファイルに `bins` を書けば不要 |
| `-c`, `--config` | 設定ファイル。`bins` / `start` / `entry` / `m1_handler` を使う |
| `-s`, `--start` | 最初に追う番地（既定は `0x0000`、または設定ファイルの `start`） |
| `-e`, `--entry` | 追加のエントリポイント（複数指定可）。シンボル名か番地。設定ファイルの `entry` に足される |
| `--auto-entry` | ジャンプテーブルなどからエントリポイントを探す（[後述](#エントリポイントの自動抽出---auto-entry)） |

`bins` で複数のファイルを置いたとき、どのファイルも置かれていない番地は出力から除外されます。`disasm` も同じです。

### エントリポイントのシンボル名

Z80 の固定の番地は、シンボル名でも指定できます。

| シンボル | 番地 | 内容 |
| --- | --- | --- |
| `RESET` / `RST0` | `0x0000` | リセット |
| `RST1` 〜 `RST6` | `0x0008` 〜 `0x0030` | `RST` の飛び先（8 バイトおき） |
| `RST7` / `IM1` | `0x0038` | `RST 0x38` / 割り込みモード 1 |
| `NMI` | `0x0066` | ノンマスカブル割り込み |

### 追う分岐

* `JP` / `JR` / `CALL` / `DJNZ`: 番地を直接書いた分岐と呼び出し
* `RST n`: 固定の番地への呼び出しとして追い、次の命令へも進む
* `RET` / `RETI` / `RETN` / `HALT`: そこで流れが終わる

`JP (HL)` のような間接分岐は、飛び先が実行時の値で決まるので追えません。飛び先は `-e` で指定するか、`--auto-entry` で探します。

### エントリポイントの自動抽出 (--auto-entry)

間接分岐の飛び先は、多くの場合ジャンプテーブルに並んでいます。`--auto-entry` は、到達できたコードの中から**分岐の定型の書き方**を探し、テーブルを読んでエントリポイントを足します。

```bash
pz80 walk -i practice.bin -e NMI --auto-entry
```

```python
# auto-entry: [push-return] @0x0195 -> 0x01B3
# auto-entry: [rst-vector] @0x019C -> 0x0018
# auto-entry: [inline-after-rst] @0x019C table=0x019D stride=2 -> 0x01A3 0x01AA 0x01AE
# auto-entry: [jp-indirect] @0x01CB table=0x01CC stride=2 -> 0x0200 0x0201 0x0202
# auto-entry: entry = -e 0x0000 -e 0x0018 -e 0x0066 -e 0x01A3 -e 0x01AA -e 0x01AE -e 0x01B3 -e 0x0200 -e 0x0201 -e 0x0202
data = [
    [0x0007, 0x0017],
    [0x0023, 0x0065],
    [0x0071, 0x018F],
    [0x019D, 0x01A2],
    [0x01CC, 0x01FF],
    [0x0206, 0x03FF],
]
```

**最後の `entry =` 行が結果**で、その上の行は見つけた根拠です（`@` は見つけた命令の番地、`->` の後ろが足したエントリポイント）。`# ` で始まる行は Python のコメントなので、出力をそのまま設定ファイルに保存できます。

> **誤ったエントリポイントは、データをコードとして読ませてしまいます。** 根拠の行を確認し、正しいものを設定ファイルの `entry` に移して使ってください。`--auto-entry` は既定では無効です。

見つける形は次のとおりです。

| 根拠の名前 | 形 | 扱い |
| --- | --- | --- |
| `jp-indirect` | `LD HL,tbl` → 添字を足す → `JP (HL)`（`IX` / `IY` も） | テーブルを読む。テーブルの番地を RAM 経由で渡す形も追う |
| `push-ret` | `PUSH HL` → `RET` | テーブルを読む |
| `inline-after-call` | `CALL disp` の直後にテーブル。`disp` が `POP HL` で取り出す | 戻り番地をテーブルの先頭として読む |
| `inline-after-rst` | `RST n` の直後にテーブル（[下記](#rst-直後のテーブル)） | 同上 |
| — | テーブルの中身が `JP nnnn` の並び | 各 `JP` の番地をエントリにする（上の形でテーブルを読むときに判定） |
| `rst-vector` | 到達したコードにある `RST n` | その飛び先をエントリにする |
| `push-return` | `LD HL,nn` → `PUSH HL`（[下記](#積んだ戻り先)） | `nn` をエントリにする |
| `sp-ret` | `LD SP,HL` → `RET` / `RETN` | 飛び先が実行時のスタックで決まるので、報告だけする |

#### RST 直後のテーブル

`RST n` の直後に `dw` のテーブルを置き、`RST` の飛び先が `POP hl` で戻り番地（＝テーブルの先頭）を取り出して飛ぶ形です。この `RST` は呼び出し元へ戻らないので、直後のバイトはデータとして扱われます。

```asm
    LD  a, 1            ; 添字
    RST 0x18
    dw  handler0, handler1, handler2   ; 命令の直後がテーブル

    org 0x0018
    ADD a, a            ; 添字を 2 倍
    POP hl              ; 戻り番地 = テーブルの先頭
    LD  e, a
    LD  d, 0
    ADD hl, de
    LD  e, (hl)
    INC hl
    LD  d, (hl)
    EX  de, hl
    JP  (hl)
```

テーブルの長さは書かれていないので、次のどれかに当たったところを終わりとします。

* イメージの外を指す値、または `0x0000`
* 直前と同じ値（離れた位置で同じ値が出るのは構いません）
* 既に命令と分かっている番地に重なる
* テーブルより後ろを指す飛び先のうち、いちばん近いものに達した

#### 積んだ戻り先

`LD hl, nn` の直後に `PUSH hl` があると、`nn` をエントリに加えます。後で `RET` したときの戻り先になるからです。積んだ値をすぐ `POP` で戻している場合（遅延ループの回数など）は除きますが、誤検出の余地は残るので根拠の行を確認してください。

### 追えないもの

* 間接分岐（`JP (HL)` など）の飛び先で、`--auto-entry` が見つけられないもの
* 割り込みモード 2 のベクタテーブル経由の呼び出し
* `LD SP,HL` → `RET` のように、飛び先が実行時のスタックで決まるもの

これらは `-e` か設定ファイルの `entry` で指定してください。

### 追えなかった分岐先の報告

分岐先を読めなかったときは、`# unresolved:` の行で報告します。

```
# unresolved: 0x2000
# unresolved: branch targets above could not be decoded.
# unresolved: a ROM file may be missing from bins, or an entry is wrong.
data = [
    ...
```

**正常なら 1 行も出ません。** 出たら何かがおかしい合図です。いちばん多い原因は、`bins` に ROM ファイルを書き忘れたことです。上の例なら、`0x2000` に置くはずのファイルが足りていません。
