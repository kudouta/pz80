# ROM 解析の流れ

> pz80 で ROM を読み解く**手順の通し例**です。各コマンドの詳細は [cli.md](cli.md)、設定ファイルは [config.md](config.md) を参照してください。

練習用の小さな ROM を作り、次の順に進めます。手元でそのまま再現できます。

1. 練習用 ROM を作る
2. `walk` でコードとデータを分ける
3. `disasm` で逆アセンブルして眺める
4. 分かったことを設定ファイルに書く（名前・コメント・表の形）
5. 組み直して、元の ROM と一致することを確かめる

実際の解析では、1 が手元の ROM ファイルに置き換わり、3〜5 を何度も繰り返します。

## 1. 練習用 ROM を作る

次のソースを `practice.asm` として保存します。1KB の ROM で、ゲームの状態で処理を切り替える表と、タスクを切り替える表を持っています。

```asm
; 練習用 ROM（1KB）
FRAME:   EQU 0xC000         ; フレームカウンタ（RAM）
STATE:   EQU 0xC001         ; ゲームの状態 0-2
TASK:    EQU 0xC002         ; 実行するタスクの番号 0-2
INT_ON:  EQU 0xE000         ; 割り込み許可（書き込み）

        org 0x0000
        di
        ld   sp, 0xC400
        im   1
        jp   main

        org 0x0018           ; RST 0x18: 直後に置いた表で分岐する
        add  a, a
        pop  hl              ; 戻りアドレス = 表の先頭
        ld   e, a
        ld   d, 0
        add  hl, de
        ld   e, (hl)
        inc  hl
        ld   d, (hl)
        ex   de, hl
        jp   (hl)

        org 0x0066           ; NMI
        push af
        ld   a, (FRAME)
        inc  a
        ld   (FRAME), a
        pop  af
        retn

        org 0x0190
main:   ld   a, 1
        ld   (INT_ON), a
loop:   ld   hl, frame_end   ; 戻り先を積む
        push hl
        ld   a, (STATE)
        rst  0x18
        dw   st_title, st_play, st_over
st_title:
        ld   hl, msg_press
        call print
        ret
st_play:
        call task_run
        ret
st_over:
        xor  a
        ld   (STATE), a
        ret
frame_end:
        ld   a, (FRAME)
        and  a
        jr   z, frame_end
        jr   loop

print:  ret

task_run:
        ld   a, (TASK)
        add  a, a
        ld   e, a
        ld   d, 0
        ld   hl, task_tbl
        add  hl, de
        ld   e, (hl)
        inc  hl
        ld   d, (hl)
        ex   de, hl
        jp   (hl)
task_tbl:
        dw   task_player, task_foe, task_sound

        org 0x0200
task_player:
        ret
task_foe:
        ret
task_sound:
        ld   hl, snd_tbl
        ret

        org 0x0300
msg_press:
        db   "PRESS START", 0
snd_tbl:
        db   4
        dw   song0
        db   0
        db   6
        dw   song1
        db   0
song0:  db   0x10, 0x12, 0x14, 0x15, 0x17, 0x19, 0x1B, 0x1C, 0xFF
song1:  db   0x20, 0x1E, 0x1C, 0x1B, 0x19, 0x17, 0xFF
```

1KB にそろえてアセンブルします。ここから先は、このソースを見ずに `practice.bin` だけを読み解く想定です。

```bash
pz80 asm -f practice.asm -o practice.bin -s 1024
```

## 2. コードとデータを分ける

`walk` は、エントリポイントから制御の流れを追い、たどり着けなかった範囲をデータとして出します。リセット（`0x0000`）からは自動で追うので、NMI を `-e` で足します。

```bash
pz80 walk -i practice.bin -e NMI
```

```python
data = [
    [0x0009, 0x0017],
    [0x0023, 0x0065],
    [0x0071, 0x018F],
    [0x01AA, 0x01BA],
    [0x01BC, 0x03FF],
]
```

`0x01AA` 以降がほとんどデータになっています。原因は `RST 0x18` です。`0x0018` の処理は直後の表を使って飛ぶので、飛び先は実行するまで分かりません。そのうえ `walk` は `RST` を普通の呼び出しとして扱うので、直後の表（`0x019D` から）を命令として読み進めてしまいます。

`--auto-entry` を付けると、こうした分岐の定型の書き方を探して、表から飛び先を読みます。

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
    [0x0009, 0x0017],
    [0x0023, 0x0065],
    [0x0071, 0x018F],
    [0x019D, 0x01A2],
    [0x01CC, 0x01FF],
    [0x0206, 0x03FF],
]
```

`entry =` の上にある 4 行が finding lines で、見つけた分岐の形を 1 件ずつ示しています。

* `push-return`: `0x0195` で積んだ戻り先 `0x01B3`
* `rst-vector`: `0x019C` の `RST 0x18` の飛び先
* `inline-after-rst`: `RST 0x18` の直後の表（`0x019D`）にある 3 つの飛び先
* `jp-indirect`: `JP (hl)` で使う表（`0x01CC`）にある 3 つの飛び先

データとして残ったのは、2 つの表と、文字列・曲のデータ、あいだの空き（`0x00`）です。この出力を設定ファイルとして保存します。

```bash
pz80 walk -i practice.bin -e NMI --auto-entry > practice_cfg.py
```

> **finding lines は確認してください。** 誤ったエントリポイントは、データをコードとして読ませてしまいます。確認できたものは、設定ファイルの `entry` に移しておきます（4 で行います）。

## 3. 逆アセンブルして眺める

```bash
pz80 disasm -i practice.bin -c practice_cfg.py
```

```
0x0190 3E 01        L_0190:  LD a, 0x01
0x0192 32 00 E0              LD (0xE000), a
0x0195 21 B3 01     L_0195:  LD hl, 0x01B3
0x0198 E5                    PUSH hl
0x0199 3A 01 C0              LD a, (0xC001)
0x019C DF                    RST 0x18
0x019D A3                    db 0xA3 ; [.]
0x019E 01                    db 0x01 ; [.]
0x019F AA                    db 0xAA ; [.]
0x01A0 01                    db 0x01 ; [.]
0x01A1 AE                    db 0xAE ; [.]
0x01A2 01                    db 0x01 ; [.]
0x01A3 21 00 03              LD hl, 0x0300
0x01A6 CD BB 01              CALL L_01BB
0x01A9 C9                    RET
```

命令とデータは分かれましたが、まだ読みにくい状態です。

* アドレスが数字のまま（`0xC001` が何なのか、`0x0300` に何があるのか）
* 表が 1 バイトずつの `db` で、飛び先を指していることが分からない

## 4. 分かったことを設定ファイルに書く

読み進めて分かったことを、`practice_cfg.py` に書き足していきます。

**出力の `.asm` には手を入れません。** 逆アセンブルし直すと消えてしまうからです。名前もコメントも設定ファイルに書き、出力はいつでも作り直せるものとして扱います。

```python
# practice_cfg.py
entry = ["NMI", 0x0018, 0x01A3, 0x01AA, 0x01AE, 0x01B3, 0x0200, 0x0201, 0x0202]

data = [
    [0x0009, 0x0017],
    [0x0023, 0x0065],
    [0x0071, 0x018F],
    {"range": [0x019D, 0x01A2], "fmt": "w"},        # RST 0x18 直後の表
    {"range": [0x01CC, 0x01D1], "fmt": "w"},        # タスクの表
    {"range": [0x01D2, 0x01FF], "per_line": 16},
    {"range": [0x0206, 0x02FF], "per_line": 16},
    [0x0300, 0x030B],                               # "PRESS START", 0
    {"range": [0x030C, 0x0313], "fmt": "b w b"},    # 曲の表
    {"range": [0x0314, 0x0323], "per_line": 8},     # 楽譜
    {"range": [0x0324, 0x03FF], "per_line": 16},
]

labels = {
    0x0190: "Main",
    0x0195: "GameLoop",
    0x01A3: "StTitle",
    0x01AA: "StPlay",
    0x01AE: "StOver",
    0x01B3: "WaitFrame",
    0x01BB: "Print",
    0x01BC: "RunTask",
    0x01CC: "TaskTbl",
    0x0200: "TaskPlayer",
    0x0201: "TaskFoe",
    0x0202: "TaskSound",
    0x0300: "MsgPress",
    0x030C: "SongTbl",
    0x0314: "Song0",
    0x031D: "Song1",
    0x0018: {"name": "Rst18_Dispatch", "imm": False},
}

equ = {
    0xC000: "Frame",
    0xC001: "State",
    0xC002: "Task",
    0xE000: {"w": "IntOn"},
}

comments = {
    0x0195: {"block": "メインループ: 状態ごとの処理へ分岐する"},
    0x019C: "直後の表で分岐する（戻ってこない）",
    0x030C: {"block": "曲の表: 速度, 楽譜, 未使用"},
}
```

使ったキーは次のとおりです。

| キー | 書いたこと |
| --- | --- |
| [`entry`](config.md#entry) | 2 で確認したエントリポイント。ラベルが付く |
| [`data`](config.md#data) | 表は `fmt` で `dw` に、長いバイト列は `per_line` でまとめる |
| [`labels`](config.md#labels) | ROM の中のアドレスの名前 |
| [`equ`](config.md#equ) | RAM と I/O のアドレスの名前 |
| [`comments`](config.md#comments) | アドレスでは表せない意味 |

`-n` を付けると、アドレスとバイト列の無い、組み直せる形で出ます。

```bash
pz80 disasm -i practice.bin -c practice_cfg.py -n
```

```asm
Frame: EQU 0xC000
State: EQU 0xC001
Task: EQU 0xC002
IntOn: EQU 0xE000
org 0x0000
    DI
    LD sp, 0xC400
    IM 1
    JP L_0190@Main
    ...
L_0190@Main:
    LD a, 0x01
    LD (IntOn), a
; メインループ: 状態ごとの処理へ分岐する
L_0195@GameLoop:
    LD hl, L_01B3@WaitFrame
    PUSH hl
    LD a, (State)
    RST 0x18 ; 直後の表で分岐する（戻ってこない）
    dw L_01A3@StTitle
    dw L_01AA@StPlay
    dw L_01AE@StOver
L_01A3@StTitle:
    LD hl, L_0300@MsgPress
    CALL L_01BB@Print
    RET
    ...
; 曲の表: 速度, 楽譜, 未使用
L_030C@SongTbl:
    db 0x04 ; [.]
    dw L_0314@Song0
    db 0x00 ; [.]
    db 0x06 ; [.]
    dw L_031D@Song1
    db 0x00 ; [.]
L_0314@Song0:
    db 0x10, 0x12, 0x14, 0x15, 0x17, 0x19, 0x1B, 0x1C
    db 0xFF
L_031D@Song1:
    db 0x20, 0x1E, 0x1C, 0x1B, 0x19, 0x17, 0xFF
```

RAM・I/O・ルーチン・表のアドレスが名前になり、表からは飛び先の名前が読めるようになりました。

## 5. 組み直して確かめる

出力をファイルに書き、アセンブルして、元の ROM と比べます。

```bash
pz80 disasm -i practice.bin -c practice_cfg.py -n -o practice_out.asm
pz80 asm -f practice_out.asm -o practice_out.bin -s 1024
python -c "import pathlib as p; print(p.Path('practice.bin').read_bytes() == p.Path('practice_out.bin').read_bytes())"
```

```
True
```

**`entry` / `data` / `labels` / `equ` / `comments` をどう書いても、出力を組み直すと元の ROM と一致します。** どれもバイト列を変えないからです。例外は 2 つあります。

* [`m1_handler`](config.md#m1_handler) で復号した ROM は、復号後のバイト列で組み直されるので一致しません。
* [`output`](config.md#output) で出力の形式を変えた場合は、その形式しだいです。

## 繰り返す

実際の解析では、3〜5 を何度も繰り返します。

* 読んで分かったことを設定ファイルに足し、逆アセンブルし直します。設定ファイルが大きくなったら[分割](config.md#設定ファイルを分割する)できます。
* データと思っていた範囲がコードだと分かったら、`entry` にアドレスを足して `walk` からやり直します。
* `walk` の出力に `# unresolved:` の行が出たら、`bins` に ROM ファイルを書き忘れていないか確かめてください（[cli.md](cli.md#追えなかった分岐先の報告)）。
