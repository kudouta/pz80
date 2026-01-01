import pytest

from pz80.asm import assemble
from pz80.z80 import Z80


@pytest.fixture
def asm_process(assembler):
    """アセンブルプロセスを実行するヘルパーフィクスチャ"""
    def _process(src_data, to_pass1=False):
        asm_list = assembler.pass0(src_data)
        assembler.equ(asm_list)
        assembler.pass1(asm_list)
        if not to_pass1:
            assembler.pass2(asm_list)
        return asm_list
    return _process

def test_tokenize_basic(assembler):
    """基本的なトークン化のテスト"""
    src = "ld a, 0x10"
    expected = ["ld", "a", ",", "0x10"]
    assert assembler.tokenize(src) == expected


def test_tokenize_with_comment(assembler):
    """コメント除去のテスト"""
    src = "nop ; this is comment"
    expected = ["nop"]
    assert assembler.tokenize(src) == expected


def test_asm2op_nop(assembler):
    """NOP命令の変換テスト"""
    # nop -> 0x00
    item = {"line": 1, "asm": ["nop"]}
    op, _ = assembler.asm2op(item)
    assert op == [0x00]


def test_asm2op_ld_immediate(assembler):
    """即値ロード命令の変換テスト"""
    # ld b, 0x10 -> 0x06 0x10
    item = {"line": 1, "asm": ["ld", "b", ",", "0x10"]}
    op, _ = assembler.asm2op(item)
    assert op == [0x06, 0x10]


@pytest.mark.parametrize(
    "instruction, expected_opcode",
    [
        (["ld", "a", ",", "h"], [0x7C]),
        (["ld", "b", ",", "e"], [0x43]),
        (["add", "a", ",", "l"], [0x85]),
        (["sub", "b"], [0x90]),
        (["inc", "de"], [0x13]),
        (["dec", "hl"], [0x2B]),
        (["ex", "de", ",", "hl"], [0xEB]),
        (["push", "af"], [0xF5]),
        (["pop", "ix"], [0xDD, 0xE1]),
        (["ret", "c"], [0xD8]),
        (["nop"], [0x00]),
    ],
)
def test_instructions_with_only_reserved_words(asm_process, instruction, expected_opcode):
    """予約語のみで構成される命令のアセンブルをテストする"""
    src = [{"line": 1, "asm": instruction}]
    asm_list = asm_process(src, to_pass1=True)
    assert asm_list[0]["opcode"] == expected_opcode


def test_pass0_error_reserved_word(assembler):
    """予約語をラベルに使用した場合のエラーテスト"""
    # 'a' はレジスタ名なので予約語
    src = [{"line": 1, "asm": ["a", ":"]}]
    
    with pytest.raises(ValueError, match="Reserved word"):
        assembler.pass0(src)


# Z80クラスから予約語の完全なリストを取得
cpu_instance = Z80()
all_reserved_words = cpu_instance.reserved

@pytest.mark.parametrize("reserved_word", all_reserved_words)
def test_pass0_error_for_all_reserved_words(assembler, reserved_word):
    """全ての予約語がラベルとして使用できないことを網羅的にテストする"""
    src = [{"line": 1, "asm": [reserved_word, ":"]}]
    with pytest.raises(ValueError, match="Reserved word"):
        assembler.pass0(src)


def test_pass0_error_invalid_label_start(assembler):
    """数字で始まる不正なラベルのエラーテスト"""
    src = [{"line": 1, "asm": ["1label", ":"]}]
    
    with pytest.raises(ValueError, match="Invalid label format"):
        assembler.pass0(src)


def test_full_assembly_process(assembler):
    """アセンブル処理全体の統合テスト"""
    # テスト用ソースコード構造
    # ORG 0x0100
    # LABEL:
    #   ld a, 0x10
    #   jp LABEL
    
    src_data = [
        {"line": 1, "asm": ["org", "0x0100"]},
        {"line": 2, "asm": ["LABEL", ":"]},
        {"line": 3, "asm": ["ld", "a", ",", "0x10"]},
        {"line": 4, "asm": ["jp", "LABEL"]},
    ]

    # 1. pass0: ラベル抽出と構造化
    asm_list = assembler.pass0(src_data)
    
    # pass0の結果検証
    # ORG行はpass0で処理されリストには残らないため、ラベル行, 命令行, 命令行 の3要素になる
    assert len(asm_list) == 3
    assert asm_list[0]["base"] == 0x100

    # 2. equ: 定数置換 (今回はなし)
    assembler.equ(asm_list)

    # 3. pass1: オペコード生成とアドレス仮決定
    assembler.pass1(asm_list)
    
    # pass1の結果検証
    # ld a, 0x10 -> 3E 10 (2バイト)
    assert asm_list[1]["opcode"] == [0x3E, 0x10]
    assert asm_list[1]["offset"] == 0
    
    # jp LABEL -> C3 00 00 (3バイト, アドレスはまだ仮の0000)
    assert asm_list[2]["opcode"] == [0xC3, 0x00, 0x00]
    # オフセットは前の命令(2bytes)の分だけ進んでいるはず
    assert asm_list[2]["offset"] == 2

    # 4. pass2: ラベルアドレス解決
    assembler.pass2(asm_list)

    # pass2の結果検証
    # pass2はリスト内の辞書を直接更新する
    # LABELのアドレスは 0x0100 (ORG) + 0 (offset) = 0x0100
    # jp 0x0100 -> C3 00 01 (リトルエンディアン)
    expected_opcode = [0xC3, 0x00, 0x01]
    assert asm_list[2]["opcode"] == expected_opcode


def test_tokenize_string_literal(assembler):
    """文字列リテラルのトークン化テスト"""
    src = 'db "Hello, World!", 13, 10'
    expected = ["db", '"Hello, World!"', ",", "13", ",", "10"]
    assert assembler.tokenize(src) == expected


def test_db_instruction_string(asm_process):
    """DB命令（文字列）のアセンブルテスト"""
    src = [{"line": 1, "asm": ["org", "0x00"]}, {"line": 2, "asm": ["db", '"TEST"']}]
    asm_list = asm_process(src, to_pass1=True)
    
    # "TEST" -> 0x54, 0x45, 0x53, 0x54 に変換される
    expected_opcode = [0x54, 0x45, 0x53, 0x54]
    assert asm_list[0]["opcode"] == expected_opcode
    assert asm_list[0]["offset"] == 0
    assert len(asm_list[0]["opcode"]) == 4


def test_db_instruction_mixed(asm_process):
    """DB命令（文字列と数値の混合）のアセンブルテスト"""
    src = [{"line": 1, "asm": ["org", "0x00"]}, {"line": 2, "asm": ["db", '"A"', ",", "1", ",", "'B'", ",", "2"]}]
    asm_list = asm_process(src, to_pass1=True)
    
    # "A", 1, 'B', 2 -> 0x41, 1, 0x42, 2 に変換される
    expected_opcode = [0x41, 1, 0x42, 2]
    assert asm_list[0]["opcode"] == expected_opcode
    assert asm_list[0]["offset"] == 0
    assert len(asm_list[0]["opcode"]) == 4


def test_dw_instruction_numeric(asm_process):
    """DW命令（数値）のアセンブルテスト"""
    src = [{"line": 1, "asm": ["org", "0x00"]}, {"line": 2, "asm": ["dw", "0x1234", ",", "0x5678"]}]
    asm_list = asm_process(src, to_pass1=True)
    
    # 0x1234 -> 34 12 (リトルエンディアン)
    # 0x5678 -> 78 56 (リトルエンディアン)
    expected_opcode = [0x34, 0x12, 0x78, 0x56]
    assert asm_list[0]["opcode"] == expected_opcode
    assert asm_list[0]["offset"] == 0
    assert len(asm_list[0]["opcode"]) == 4


def test_dw_instruction_label(asm_process):
    """DW命令（ラベル）のアセンブルテスト"""
    # ORG 0x1000 を設定
    # DATAラベルで dw 0x1234 を定義
    #       dw DATA
    src_data = [
        {"line": 1, "asm": ["org", "0x1000"]},
        {"line": 2, "asm": ["DATA", ":", "dw", "0x1234"]},
        {"line": 3, "asm": ["dw", "DATA"]},
    ]
    
    asm_list = asm_process(src_data)
    
    # 2行目: dw 0x1234 -> 34 12
    assert asm_list[1]["opcode"] == [0x34, 0x12]
    
    # 3行目: dw DATA -> 00 10 (0x1000 リトルエンディアン)
    assert asm_list[2]["opcode"] == [0x00, 0x10]


def test_dw_instruction_multiple_labels(asm_process):
    """DW命令（複数ラベル）のアセンブルテスト"""
    # ORG 0x1000 を設定
    # L1: dw 0x1111
    # L2: dw 0x2222
    #     dw L1, L2
    src_data = [
        {"line": 1, "asm": ["org", "0x1000"]},
        {"line": 2, "asm": ["L1", ":", "dw", "0x1111"]},
        {"line": 3, "asm": ["L2", ":", "dw", "0x2222"]},
        {"line": 4, "asm": ["dw", "L1", ",", "L2"]},
    ]
    
    asm_list = asm_process(src_data)
    
    # 2行目: dw 0x1111 -> 11 11
    assert asm_list[1]["opcode"] == [0x11, 0x11]
    
    # 3行目: dw 0x2222 -> 22 22
    assert asm_list[3]["opcode"] == [0x22, 0x22]

    # 4行目: dw L1, L2
    # L1アドレス: 0x1000 -> 00 10
    # L2アドレス: 0x1000 + 2 = 0x1002 -> 02 10
    expected_opcode = [0x00, 0x10, 0x02, 0x10]
    assert asm_list[4]["opcode"] == expected_opcode


def test_undefined_label_in_instruction(asm_process):
    """未定義ラベル（通常命令）のエラーテスト"""
    # ld a, UNDEFINED_LABEL
    src = [{"line": 1, "asm": ["ld", "a", ",", "UNDEFINED_LABEL"]}]
    
    with pytest.raises(ValueError, match="Undefined symbol"):
        asm_process(src, to_pass1=True)


def test_undefined_label_in_dw(asm_process):
    """未定義ラベル（DW命令）のエラーテスト"""
    # dw UNDEFINED_LABEL
    src = [{"line": 1, "asm": ["dw", "UNDEFINED_LABEL"]}]
    
    with pytest.raises(ValueError, match="Undefined label or invalid term"):
        asm_process(src)


def test_db_instruction_string_with_escape(asm_process):
    """DB命令（エスケープシーケンス付き文字列）のアセンブルテスト"""
    # "A\nB" は 0x41, 0x0A, 0x42 に変換される
    src = [{"line": 1, "asm": ["org", "0x00"]}, {"line": 2, "asm": ["db", '"A\\nB"']}]
    asm_list = asm_process(src, to_pass1=True)
    
    expected_opcode = [0x41, 0x0A, 0x42]
    assert asm_list[0]["opcode"] == expected_opcode
    assert asm_list[0]["offset"] == 0
    assert len(asm_list[0]["opcode"]) == 3


def test_tokenize_string_with_escaped_quote(assembler):
    """エスケープされたクォートを含む文字列リテラルのトークン化テスト"""
    src = r'db "He said \"Hello\""'
    expected = ["db", r'"He said \"Hello\""']
    assert assembler.tokenize(src) == expected


def test_tokenize_string_with_escapes(assembler):
    """エスケープシーケンスを含む文字列リテラルのトークン化テスト"""
    src = r'db "Line1\nLine2", "Tab\tSeparated", "Backslash\\"'
    expected = ["db", r'"Line1\nLine2"', ",", r'"Tab\tSeparated"', ",", r'"Backslash\\"']
    assert assembler.tokenize(src) == expected


def test_char_literal_instruction(asm_process):
    """文字リテラルを含む命令のアセンブルテスト"""
    # ld a, '0' は ld a, 0x30 と解釈され、3E 30 になる
    src = [{"line": 1, "asm": ["ld", "a", ",", "'0'"]}]
    
    asm_list = asm_process(src, to_pass1=True)
    
    expected_opcode = [0x3E, 0x30]
    assert asm_list[0]["opcode"] == expected_opcode


def test_char_literal_instruction_word(asm_process):
    """2文字リテラルを含む命令のアセンブルテスト"""
    # ld hl, 'AB' は ld hl, 0x4142 と解釈され、21 42 41 になる
    src = [{"line": 1, "asm": ["ld", "hl", ",", "'AB'"]}]
    
    asm_list = asm_process(src, to_pass1=True)
    
    expected_opcode = [0x21, 0x42, 0x41]
    assert asm_list[0]["opcode"] == expected_opcode


def test_dw_instruction_string_literal(asm_process):
    """DW命令（文字列リテラル）のアセンブルテスト"""
    # dw 'A', 'AB' は 0x0041, 0x4142 と解釈され、41 00 42 41 になる
    src = [{"line": 1, "asm": ["dw", "'A'", ",", "'AB'"]}]
    
    asm_list = asm_process(src)
    
    expected_opcode = [0x41, 0x00, 0x42, 0x41]
    assert asm_list[0]["opcode"] == expected_opcode


def test_dw_instruction_long_string_literal_error(asm_process):
    """DW命令（3文字以上の文字列リテラル）のエラーテスト"""
    src = [{"line": 1, "asm": ["dw", '"ABC"']}]

    with pytest.raises(ValueError, match="String literal in DW must be 1 or 2 characters"):
        asm_process(src, to_pass1=True)

def test_db_instruction_out_of_range_error(asm_process):
    """DB命令（範囲外の数値）のエラーテスト"""
    src = [{"line": 1, "asm": ["db", "256"]}]

    with pytest.raises(ValueError, match="DB value out of byte range"):
        asm_process(src, to_pass1=True)


def test_bit_instruction(asm_process):
    """BIT命令のテスト"""
    # bit 7, a は CB 7F になる
    src = [{"line": 1, "asm": ["bit", "7", ",", "a"]}]
    asm_list = asm_process(src, to_pass1=True)
    assert asm_list[0]["opcode"] == [0xCB, 0x7F]


def test_res_instruction(asm_process):
    """RES命令のテスト"""
    # res 3, (hl) は CB 9E になる
    src = [{"line": 1, "asm": ["res", "3", ",", "(", "hl", ")"]}]
    asm_list = asm_process(src, to_pass1=True)
    assert asm_list[0]["opcode"] == [0xCB, 0x9E]


def test_set_instruction(asm_process):
    """SET命令のテスト"""
    # set 1, c は CB C9 になる
    src = [{"line": 1, "asm": ["set", "1", ",", "c"]}]
    asm_list = asm_process(src, to_pass1=True)
    assert asm_list[0]["opcode"] == [0xCB, 0xC9]


def test_bit_ix_instruction(asm_process):
    """BIT命令(IX)のテスト"""
    # bit 0, (ix+10) は DD CB 0A 46 になる
    src = [{"line": 1, "asm": ["bit", "0", ",", "(", "ix", "+", "10", ")"]}]
    asm_list = asm_process(src, to_pass1=True)
    assert asm_list[0]["opcode"] == [0xDD, 0xCB, 0x0A, 0x46]


def test_set_iy_instruction_with_register(asm_process):
    """SET命令(IY, レジスタ指定)のテスト"""
    # set 0, (iy+10), b は FD CB 0A C0 になる
    src = [{"line": 1, "asm": ["set", "0", ",", "(", "iy", "+", "10", ")", ",", "b"]}]
    asm_list = asm_process(src, to_pass1=True)
    assert asm_list[0]["opcode"] == [0xFD, 0xCB, 0x0A, 0xC0]


def test_jr_instruction_backward(asm_process):
    """JR命令（後方ジャンプ）のアセンブルテスト"""
    # LABEL:
    #   nop
    #   jr LABEL
    #
    # Address:
    # 0000: 00 (nop)
    # 0001: 18 xx (jr)
    # jrオペランドのPCは0003。ターゲットは0000。オフセット = 0 - 3 = -3 (0xFD)
    src_data = [
        {"line": 1, "asm": ["org", "0x0000"]},
        {"line": 2, "asm": ["LABEL", ":"]},
        {"line": 3, "asm": ["nop"]},
        {"line": 4, "asm": ["jr", "LABEL"]},
    ]
    asm_list = asm_process(src_data)
    assert asm_list[2]["opcode"] == [0x18, 0xFD]


def test_equ_directive(asm_process):
    """EQU疑似命令のテスト"""
    src_data = [{"line": 1, "asm": ["VAL", ":", "equ", "0x10"]}, {"line": 2, "asm": ["ld", "a", ",", "VAL"]}]
    asm_list = asm_process(src_data, to_pass1=True)
    assert asm_list[0]["opcode"] == [0x3E, 0x10]


def test_label_with_offset(asm_process):
    """ラベルにオフセットを加算する命令のアセンブルテスト"""
    src_data = [
        {"line": 1, "asm": ["org", "0x100"]},
        {"line": 2, "asm": ["LABEL", ":", "nop"]},
        {"line": 3, "asm": ["nop"]},
        {"line": 4, "asm": ["ld", "a", ",", "(", "LABEL", "+", "1", ")"]},
    ]
    asm_list = asm_process(src_data)
    # LABELは0x100。LABEL+1は0x101。
    # ld a, (0x0101) は 3A 01 01 になる
    assert asm_list[3]["opcode"] == [0x3A, 0x01, 0x01]


def test_expression_with_precedence(asm_process):
    """乗算と加算を含む数式評価のテスト"""
    src_data = [
        {"line": 1, "asm": ["VAL", ":", "equ", "10"]},
        {"line": 2, "asm": ["dw", "5", "+", "VAL", "*", "2"]},  # 式は 5 + 10 * 2 = 25 と評価される
    ]
    asm_list = asm_process(src_data)
    # dw 25 は 19 00 になる
    assert asm_list[0]["opcode"] == [0x19, 0x00]


def test_relative_jump_out_of_range(asm_process):
    """相対ジャンプ（範囲外）のエラーテスト"""
    # jr命令のジャンプ先が遠すぎる(-132バイト)ケース
    src_data = [{"line": 1, "asm": ["org", "0x00"]}, {"line": 2, "asm": ["LABEL", ":"]}]
    # 129個のnop命令を追加して距離を稼ぐ
    src_data.extend([{"line": i, "asm": ["nop"]} for i in range(3, 132)])
    src_data.append({"line": 132, "asm": ["jr", "LABEL"]})

    with pytest.raises(ValueError, match="Relative jump out of range"):
        asm_process(src_data)


def test_conditional_jump(asm_process):
    """条件付きジャンプ命令のアセンブルテスト"""
    src_data = [
        {"line": 1, "asm": ["org", "0x100"]},
        {"line": 2, "asm": ["LABEL", ":"]},
        {"line": 3, "asm": ["jp", "z", ",", "LABEL"]},
    ]
    asm_list = asm_process(src_data)
    # jp z, 0x0100 は CA 00 01 になる
    assert asm_list[1]["opcode"] == [0xCA, 0x00, 0x01]


def test_expression_with_subtraction(asm_process):
    """減算を含む数式評価のテスト"""
    src_data = [
        {"line": 1, "asm": ["org", "0x100"]},
        {"line": 2, "asm": ["L1", ":"]},
        {"line": 3, "asm": ["nop"]},
        {"line": 4, "asm": ["L2", ":"]},
        {"line": 5, "asm": ["dw", "L2", "-", "L1"]},  # 式は L2 (0x101) - L1 (0x100) = 1 と評価される
    ]
    asm_list = asm_process(src_data)
    # dw 1 は 01 00 になる
    assert asm_list[3]["opcode"] == [0x01, 0x00]


def test_expression_with_parentheses(asm_process):
    """括弧を含む数式評価のテスト"""
    src_data = [
        {"line": 1, "asm": ["VAL", ":", "equ", "10"]},
        {"line": 2, "asm": ["dw", "(", "VAL", "+", "5", ")", "*", "2"]},  # 式は (10 + 5) * 2 = 30 と評価される
    ]
    asm_list = asm_process(src_data)
    # dw 30 は 1E 00 になる
    assert asm_list[0]["opcode"] == [0x1E, 0x00]


def test_assemble_function_basic():
    """assemble関数の基本的なテスト"""
    src = "ld a, 0x10"
    binary = assemble(src)
    assert binary == b'\x3E\x10'


def test_assemble_function_with_gap():
    """assemble関数のギャップ埋め（パディング）テスト"""
    src = """
    org 0x0000
    db 0xAA
    org 0x0002
    db 0xBB
    """
    binary = assemble(src)
    assert binary == b'\xAA\x00\xBB'


def test_assemble_function_with_equ():
    """assemble関数のEQU使用テスト"""
    src = """
    VAL: EQU 0x20
    ld a, VAL
    """
    binary = assemble(src)
    assert binary == b'\x3E\x20'


def test_assemble_function_with_label_forward_ref():
    """assemble関数のラベル前方参照テスト"""
    # 0x0000: 18 01 (jr 0x0003 -> offset 1)
    # 0x0002: 00    (nop)
    # 0x0003: 76    (halt)
    src = """
        jr TARGET
        nop
    TARGET:
        halt
    """
    binary = assemble(src)
    assert binary == b'\x18\x01\x00\x76'


def test_assemble_function_with_label_backward_ref():
    """assemble関数のラベル後方参照テスト"""
    # 0x0000: 00    (nop)
    # 0x0001: 18 FD (jr 0x0000 -> offset -3 -> 0xFD)
    src = """
    START:
        nop
        jr START
    """
    binary = assemble(src)
    assert binary == b'\x00\x18\xFD'