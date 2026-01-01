# pz80

Pythonモジュールで実装したZ80アセンブラおよび逆アセンブラです。

## 概要

pz80は、Z80アセンブリプログラミングのためのシンプルなツールセットです。以下の機能を含みます：

- **アセンブラ**: Z80アセンブリソースコードをバイナリに変換します。
- **逆アセンブラ**: バイナリファイルをZ80アセンブリニーモニックに変換します。

## Pythonモジュールとしての利点

- **クロスプラットフォーム**: Pythonが動作する環境（Windows, macOS, Linuxなど）であれば、OSを問わず動作します。
- **容易な導入**: `pip` を介して簡単にインストール・管理が可能です。
- **ライブラリ利用**: 他のPythonプロジェクトからモジュールとしてインポートし、アセンブルや逆アセンブル機能をプログラム内で動的に利用できます。

## 必要要件

- Python 3.10 以上

## インストール

リポジトリをクローンし、`pip` を使用してインストールできます：

```bash
git clone https://github.com/kudouta/pz80.git
cd pz80
pip install .
```

開発環境として `uv` を使用している場合：

```bash
uv sync
```

## 使い方

インストール後、`pz80` コマンド（または `python -m pz80`）が使用可能になります。

### アセンブラ (asm)

ソースファイルをアセンブルしてバイナリを出力します。

```bash
pz80 asm -f source.asm -o output.bin
```

**オプション:**
- `-f`, `--file`: 入力アセンブリファイル（必須）
- `-o`, `--output`: 出力バイナリファイル（必須）
- `-s`, `--size`: 出力ファイルサイズを指定（オプション）。指定サイズまで `0x00` でパディングします。

### 逆アセンブラ (disasm)

バイナリファイルを逆アセンブルします。

```bash
pz80 disasm -i input.bin
```

**オプション:**
- `-i`, `--input`: 入力バイナリファイル（必須、複数指定可）
- `-s`, `--start`: 開始アドレス（デフォルト: 0）
- `-o`, `--output`: 出力ファイル（デフォルト: 標準出力）
- `-n`, `--nodump`: ダンプ情報を非表示にし、アセンブリのみ出力
- `-c`, `--config`: データ領域などを定義した設定ファイル（オプション）

#### 設定ファイルについて

`-c` オプションでPythonモジュール形式の設定ファイルを指定することで、逆アセンブルの挙動をカスタマイズできます。

**主な設定項目:**
- `data`: `db` (データ)として扱うアドレス範囲のリスト。
- `chr`: データダンプ時に使用する文字テーブル（256文字のタプル）。
- `output`: 独自のフォーマットで出力するためのカスタム関数。

**設定ファイルの例 (`my_config.py`):**

```python
# my_config.py
from pz80 import z80

# 0x8000から0x80FF、0x8100から0x81FFをデータとして表現する例
# (指定範囲は全てDB文で展開する)
data = [
    [0x8000, 0x80FF],[0x8100, 0x81FF]
]

# 0x00-0x1Fを制御コードとして別の文字で表現する例
# (デフォルトでは '.' で表示される)
#
# デフォルトのテーブルをコピーして変更する
chr_list = list(z80.Z80().strmap)
chr_list[0x00] = '<NUL>'
chr_list[0x0D] = '<CR>'
chr_list[0x0A] = '<LF>'
chr = tuple(chr_list)

# カスタム出力関数 (例: アセンブリコードのみを出力)
def output(dis, sw):
    for p in dis:
        if p.get("label"):
            print(p["label"])
        if p.get("asm"):
            # ORG疑似命令などはopcodeがないので、インデントしない
            indent = "    " if p.get("opcode") else ""
            print(f'{indent}{p["asm"]}')
```
## ライブラリとしての使用

Pythonスクリプトから pz80 モジュールをインポートして使用する例です。

### アセンブル

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

### 逆アセンブル

```Python
from pz80 import disassemble

# バイト列を逆アセンブルして命令リストを取得
binary_data = b'\x3E\x2A\xC9'
instructions = disassemble(binary_data, start_address=0x100)
for line in instructions:
    print(line)
```

## テストの実行

テストには `pytest` を使用します。

```bash
pytest
```

`uv` を使用している場合：

```bash
uv run -m pytest
```

## 開発者向けドキュメント

### 開発環境のセットアップ

開発に必要な依存関係（テストツール、リンターなど）をインストールします。

```bash
pip install pytest pytest-cov ruff
```

### Linting (静的解析)

コードの品質チェックには `ruff` を使用します。

```bash
ruff check .
```

### アーキテクチャ概要

このプロジェクトは、責務の分離を意識したモジュール構成になっています。

```
pz80/
├── src/pz80/
│   ├── __main__.py       # CLIエントリーポイント
│   ├── asm.py            # アセンブラ本体 (2パス処理)
│   ├── disasm.py         # 逆アセンブラ本体
│   ├── evaluator.py      # 式評価器
│   ├── directives.py     # 疑似命令ハンドラ
│   └── z80.py            # Z80命令定義、予約語など
└── tests/
    ├── test_asm.py
    ├── test_disasm.py
    ├── test_evaluator.py
    └── test_directives.py
```

### アセンブラ (`asm.py`) の処理フロー

アセンブラは、伝統的な2パス方式（実際には3ステップ）でソースコードをマシン語に変換します。

1.  **Pass 0 (`pass0`)**:
    *   ソースコードをトークンに分解します。
    *   `LABEL:` 形式のラベル定義や `EQU` 疑似命令を抽出し、`labelmap` に登録します。
    *   `ORG` を解釈し、各行のベースアドレスを設定します。

2.  **EQU置換 (`equ`)**:
    *   `EQU` で定義された定数を、ソースコード全体のシンボルに置換します。

3.  **Pass 1 (`pass1`)**:
    *   各命令のサイズを計算し、すべてのラベルの最終的なアドレスを確定させます。
    *   この時点では、ラベル参照を含むオペランド（例: `jp LABEL`）は仮の値（`0x00`）で埋められます。
    *   後で値を修正すべき箇所の情報（`fixups`）を保持します。

4.  **Pass 2 (`pass2`)**:
    *   Pass 1で確定したラベルアドレスを使い、`fixups` 情報を元にオペコード内の仮の値を正しいアドレスで上書きします。

#### 式の評価 (`evaluator.py`)

`ExpressionEvaluator` クラスが、再帰下降構文解析（Recursive Descent Parsing）を用いて、`VAL * 2 + 5` のような数式を評価します。Pass 1ではラベルを `0` として計算し、Pass 2で実際の値に置き換えます。

#### 疑似命令の処理 (`directives.py`)

`DirectiveHandler` クラスが、`DB` や `DW` といった疑似命令のオペランド解析とバイト列生成を専門に担当します。

### 逆アセンブラ (`disasm.py`) の処理フロー

1.  バイナリデータをメモリイメージにロードします。
2.  1バイトずつ読み進め、`0xCB`, `0xED`, `0xDD` などのプレフィックスバイトに応じて、後続の命令バイト数を判断します。
3.  最も長く一致するオペコードを `z80.py` の `op_map` から検索します。
4.  見つかった命令定義に基づき、オペランド（即値、相対アドレスなど）をフォーマットしてアセンブリ文字列を生成します。
5.  ジャンプ命令の飛び先アドレスを解析し、`L_XXXX` 形式のラベルを自動生成します。

## ライセンス

MIT License