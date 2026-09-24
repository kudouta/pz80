# pz80

Z80用のツールを作ってみました。CLIとして動作しますが、Pythonモジュールとしても利用できます。

## 概要

pz80は下記の機能を持ちます。

* **アセンブラ**: Z80アセンブリソースコードをバイナリに変換
* **逆アセンブラ**: バイナリファイルをZ80アセンブリニーモニックに変換
* **ウォーカー**: バイナリの制御フローグラフをトレースし、コードとして到達できないデータ領域を検出

## ドキュメント

| | 内容 |
| --- | --- |
| **[CLI の使い方](guide/cli.md)** | `pz80 asm` / `disasm` / `walk` の各オプションと、設定ファイル（`bins` / `data` / `chr` / `output` / `entry` / `m1_handler`）の書き方 |
| **[アセンブリ言語仕様](guide/language.md)** | `.asm` に書ける構文。数値リテラル・疑似命令・ラベル・式の評価 |
| **[Python API](guide/python-api.md)** | モジュールとして使う場合の公開 API、戻り値の形式、複数チャンクの連結アセンブル |

## 必要要件

* Python 3.10 以上

## インストール

```bash
pip install git+https://github.com/kudouta/pz80.git
```

ソースを取得してからインストールすることもできます。

```bash
cd pz80
pip install .
```

## 使い方の概要

### コマンドラインから

```bash
# アセンブル
pz80 asm -f source.asm -o output.bin

# 逆アセンブル
pz80 disasm -i rom.bin

# データ領域の検出（結果は disasm の設定ファイルにそのまま使える）
pz80 walk -i rom.bin -e NMI
```

各オプションと設定ファイルの詳細は **[guide/cli.md](guide/cli.md)** を参照してください。

### Python モジュールとして

```python
from pz80 import assemble, disassemble, walk

binary = assemble("    ORG 0x100\n    LD A, 42\n    RET\n")
lines = disassemble(binary, start_address=0x100)
data_regions = walk(binary, extra_entries=["NMI"])
```

`Asm` クラスを直接使うと、行リストや複数チャンクからのアセンブル、シンボル表の参照、
アセンブル結果の一覧表示などができます。詳細は **[guide/python-api.md](guide/python-api.md)** を参照してください。

### アセンブリソース

```asm
ORG 0x0100          ; 開始アドレス設定
WIDTH:  EQU 8       ; 定数定義
START:              ; ラベル定義
    LD A, WIDTH
    JP START
```

書ける構文の一覧は **[guide/language.md](guide/language.md)** を参照してください。

## ソースコード内の言語 / Language in the source

**コメントと docstring は日本語です。** 作者自身が保守しやすい形を優先しているため、英語へ変更する予定はありません。

利用者から見える部分はすべて英語です。CLI のヘルプ・エラーメッセージ・`--auto-entry` の報告行、そして API の名前（関数名・引数名・キー名）が該当します。**この境界はテストで検査しているので、日本語の文字列が利用者に届くことはありません。**

> **Note for non-Japanese readers**: comments and docstrings in `src/` are written in Japanese, by the author's deliberate choice, and will not be translated. Everything you interact with — CLI help, error messages, tool output, and all API names — is in English. A test enforces that no Japanese string reaches the user.

## サポートについて / Support policy

**このリポジトリは公開していますが、サポートは行いません。** Issue は開いていますが、
返信や対応をお約束できません。作者個人の道具を、使える形で置いてあるものだと
お考えください。MIT License の範囲で自由にご利用・改変いただけます。急ぎの用途や
確実な修正が必要な場合は、fork してお使いください。

> **Note**: this repository is published as-is, with no support. Issues are open, but
> a reply is not guaranteed. It is one person's tool, made available under the MIT
> License for anyone who finds it useful. If you need something fixed, fork it.

## ライセンス

本プロジェクトは [MIT License](LICENSE) の下で公開されています。
