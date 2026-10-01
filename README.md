# pz80

Z80 のアセンブラ・逆アセンブラです。ROM を逆アセンブルして読み解き、組み直して元と同じバイト列に戻すところまでを、1 つの道具で扱えます。コマンドとしても、Python のモジュールとしても使えます。

## 特徴

* **組み直せる逆アセンブル**：`disasm` の出力はそのまま `asm` でアセンブルでき、元のバイナリと一致します。
* **コードとデータの切り分け**：`walk` がエントリポイントから制御の流れを追い、データの範囲を出します。`--auto-entry` を付けると、ジャンプテーブルなどから飛び先を探します。
* **解析の結果は設定ファイルに残す**：番地の名前・コメント・表の形（`dw` の並びなど）を Python の設定ファイルに書くと、逆アセンブルの出力に反映されます。出力を作り直しても消えません。
* **複数の ROM チップ**：ROM ファイルをそれぞれの番地に置けます。命令のバイトだけを暗号化した ROM のうち、番地とバイト値から復号できる方式なら、その復号を関数で書けます。
* **Python から拡張できるアセンブラ**：`INCLUDE` やマクロに当たる処理は、Python でソースを組み立てて渡します。条件アセンブル（`IF` / `ELSEIF` / `-D`）はアセンブラ自身が持っています。

## このリポジトリについて

このリポジトリは、作者の作業用リポジトリから公開する範囲だけを切り出した**スナップショット**です。リリースごとに 1 コミットを積みます。開発の履歴・テスト・開発用の文書は含みません。各版には `v0.4.64` のようなタグが付いています。

**サポートは行いません。** Issue は開いていますが、返信や対応はお約束できません。作者個人の道具を、使える形で置いてあるものです。MIT License の範囲で自由に使い、変えてください。確実な修正が必要な場合は fork してお使いください。

> **Note**: this repository is a snapshot of the author's working repository, published as-is with no support. Issues are open, but a reply is not guaranteed. It is one person's tool, made available under the MIT License for anyone who finds it useful. If you need something fixed, fork it.

## インストール

Python 3.10 以上が必要です。

```bash
pip install git+https://github.com/kudouta/pz80.git
```

特定の版を入れるときは、末尾にタグを付けます（`pz80.git@v0.4.64` など）。ソースを取得してからインストールすることもできます。

```bash
cd pz80
pip install .
```

## 使ってみる

```bash
# アセンブル
pz80 asm -f source.asm -o output.bin

# コードとデータを切り分けて、設定ファイルとして保存
pz80 walk -i rom.bin -e NMI --auto-entry > rom_config.py

# 設定ファイルを使って逆アセンブル（-n で組み直せる形だけを出す）
pz80 disasm -i rom.bin -c rom_config.py -n -o rom.asm
```

ROM を読み解く一連の手順は、練習用の ROM を使って **[ROM 解析の流れ](guide/workflow.md)** で説明しています。

Python からは次のように使えます。

```python
from pz80 import assemble, disassemble, walk

binary = assemble("    ORG 0x100\n    LD A, 42\n    RET\n")
lines = disassemble(binary, start_address=0x100)
data_regions = walk(binary, extra_entries=["NMI"])
```

## ドキュメント

| | 内容 |
| --- | --- |
| **[ROM 解析の流れ](guide/workflow.md)** | 練習用 ROM で `walk` → `disasm` → 名前付け → 組み直しを通す |
| **[CLI リファレンス](guide/cli.md)** | `asm` / `disasm` / `walk` のオプションと出力 |
| **[設定ファイル](guide/config.md)** | `-c` で渡す設定ファイルの全キー（`bins` / `data` / `labels` / `equ` / `comments` など） |
| **[アセンブリ言語](guide/language.md)** | `.asm` に書ける構文。数値・疑似命令・条件アセンブル・ラベル・式 |
| **[Python API](guide/python-api.md)** | モジュールとして使うときの関数・クラスと戻り値 |
| **[変更履歴](CHANGELOG.md)** | 版ごとの、利用者から見た変化 |

## ソースコード内の言語 / Language in the source

**コメントと docstring は日本語です。** 作者自身が保守しやすい形を優先しているため、英語へ変更する予定はありません。

利用者から見える部分はすべて英語です。CLI のヘルプ・エラーメッセージ・`--auto-entry` が出す行、そして API の名前（関数名・引数名・キー名）が該当します。**この境界はテストで検査しているので、日本語の文字列が利用者に届くことはありません。**

> **Note for non-Japanese readers**: comments and docstrings in `src/` are written in Japanese, by the author's deliberate choice, and will not be translated. Everything you interact with — CLI help, error messages, tool output, and all API names — is in English. A test enforces that no Japanese string reaches the user.

## ライセンス

本プロジェクトは [MIT License](LICENSE) の下で公開されています。
