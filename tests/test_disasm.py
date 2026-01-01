from unittest.mock import patch

import pytest

from pz80.disasm import disassemble


@pytest.fixture
def disasm_exec(disassembler):
    """逆アセンブル実行を簡略化するヘルパーフィクスチャ"""
    def _exec(binary, start_addr=0x0000):
        return disassembler.exec(start_addr, binary, len(binary))
    return _exec

def test_disasm_nop(disasm_exec):
    """NOP命令(1バイト)の逆アセンブルテスト"""
    result = disasm_exec([0x00])
    
    # result[0] は ORG
    assert result[1]["opcode"] == [0x00]
    assert "nop" in result[1]["asm"].lower()

def test_disasm_ld_b_n(disasm_exec):
    """2バイト命令の逆アセンブルテスト"""
    result = disasm_exec([0x06, 0x10])
    
    assert result[1]["opcode"] == [0x06, 0x10]
    assert "ld b, 0x10" == result[1]["asm"].lower()

def test_disasm_ld_bc_nn(disasm_exec):
    """3バイト命令の逆アセンブルテスト"""
    result = disasm_exec([0x01, 0x34, 0x12])
    
    assert result[1]["opcode"] == [0x01, 0x34, 0x12]
    assert "ld bc, 0x1234" == result[1]["asm"].lower()

def test_disasm_ix_instruction(disasm_exec):
    """4バイト命令の逆アセンブルテスト"""
    result = disasm_exec([0xDD, 0x21, 0x34, 0x12])
    
    assert result[1]["opcode"] == [0xDD, 0x21, 0x34, 0x12]
    assert "ld ix, 0x1234" == result[1]["asm"].lower()

def test_disasm_invalid_opcode(disasm_exec):
    """不正なオペコードの逆アセンブルテスト"""
    # ED FF (EDはプレフィックスだがFFは無効)
    result = disasm_exec([0xED, 0xFF])
    
    # 1バイト目 ED がエラーとして処理される
    assert "db 0xED ; Invalid Opcode" in result[1]["asm"]
    assert result[1]["opcode"] == [0xED]
    
    # 2バイト目 FF (rst 0x38) が次の命令として処理される
    assert result[2]["opcode"] == [0xFF]
    assert "rst 0x38" in result[2]["asm"].lower()

def test_disasm_handle_3bytes_error_path(disassembler, disasm_exec):
    """_handle_3bytesがNoneを返すケースのテスト"""
    # 3バイト長だが、typeもjmpも指定されていない定義を注入
    broken_key = (0x00, 0x00, 0x00)
    broken_value = {
        "code": [0x00, 0x00, 0x00], 
        "bytes": 3, 
        "asm": ["broken"]
    }
    
    binary = [0x00, 0x00, 0x00]
    
    with patch.dict(disassembler.cpu._op_map, {broken_key: broken_value}):
        # op2asmは定義を見つけるが、_handle_3bytesはNoneを返す
        # execは3バイトでのマッチに失敗し、1バイト(nop)として処理するはず
        result = disasm_exec(binary)
        
        # 最初の命令がnop (00) であること
        assert result[1]["opcode"] == [0x00]
        assert "nop" in result[1]["asm"].lower()


def test_disasm_jr_relative(disasm_exec):
    """相対ジャンプ命令の逆アセンブルテスト"""
    # jr 0x0100 (PC=0x0102からのオフセット-2) -> 18 FE
    result = disasm_exec([0x18, 0xFE], start_addr=0x0100)
    assert result[1]["opcode"] == [0x18, 0xFE]
    assert "jr l_0100" == result[1]["asm"].lower()


def test_disasm_cb_instruction(disasm_exec):
    """ビット操作命令(CB)の逆アセンブルテスト"""
    # rlc b -> CB 00
    result = disasm_exec([0xCB, 0x00])
    assert result[1]["opcode"] == [0xCB, 0x00]
    assert "rlc b" == result[1]["asm"].lower()


def test_disasm_ed_instruction(disasm_exec):
    """拡張命令(ED)の逆アセンブルテスト"""
    # ldir -> ED B0
    result = disasm_exec([0xED, 0xB0])
    assert result[1]["opcode"] == [0xED, 0xB0]
    assert "ldir" == result[1]["asm"].lower()


def test_disasm_ddcb_instruction(disasm_exec):
    """IXビット操作命令(DDCB)の逆アセンブルテスト"""
    # set 0, (ix+10) -> DD CB 0A C6
    result = disasm_exec([0xDD, 0xCB, 0x0A, 0xC6])
    assert result[1]["opcode"] == [0xDD, 0xCB, 0x0A, 0xC6]
    # 出力フォーマットの確認 (set 0, (ix+0x0a))
    assert "set 0, (ix+0x0a)" == result[1]["asm"].lower()


def test_disasm_longest_match(disasm_exec):
    """最長一致のテスト。短い命令が長い命令のプレフィックスではないが、探索アルゴリズムを検証する。"""
    # ED 70 は IN (C) (2バイト) であり、
    # 不正なED と LD (HL),B (70h) の組み合わせではないことを確認する。
    result = disasm_exec([0xED, 0x70])

    # 結果は ORG と IN (C) の2行になるはず
    assert len(result) == 2
    assert result[1]["opcode"] == [0xED, 0x70]
    assert "in (c)" == result[1]["asm"].lower()


def test_disasm_truncated_instruction(disasm_exec):
    """途中で切れた命令のテスト"""
    # 01 34 は LD BC, nn (3バイト) の途中で切れている。
    # 01 は不正なオペコードとして db 0x01 となり、
    # 次の 34 (inc (hl)) が正しく逆アセンブルされることを確認する。
    result = disasm_exec([0x01, 0x34])

    # 結果は ORG, db, inc (hl) の3行になるはず
    assert len(result) == 3
    assert "db 0x01 ; Invalid Opcode" in result[1]["asm"]
    assert result[1]["opcode"] == [0x01]

    assert result[2]["opcode"] == [0x34]
    assert "inc (hl)" == result[2]["asm"].lower()


def test_disassemble_function_basic():
    """disassemble関数の基本的なテスト"""
    data = b'\x3E\x10'  # ld a, 0x10
    lines = disassemble(data)
    assert any("ld a, 0x10" in line.lower() for line in lines)


def test_disassemble_function_label_resolution():
    """disassemble関数のラベル解決テスト"""
    # 0x1000: 18 FE  -> jr 0x1000 (自分自身へのジャンプ)
    data = b'\x18\xFE'
    lines = disassemble(data, start_address=0x1000)
    
    # ラベル定義 "L_1000:" が含まれているか
    assert any("l_1000:" in line.lower() for line in lines)
    # 命令でラベルが使用されているか "jr l_1000"
    assert any("jr l_1000" in line.lower() for line in lines)