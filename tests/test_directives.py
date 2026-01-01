from unittest.mock import Mock

import pytest

from pz80.asm import Asm
from pz80.directives import DirectiveHandler


@pytest.fixture
def handler():
    """DirectiveHandlerのインスタンスを提供するフィクスチャ"""
    # DirectiveHandlerはAsmインスタンスを必要とするため、ここで生成します。
    # Pass 2のテストでは、Asmのメソッドをモックします。
    asm_instance = Asm()
    return DirectiveHandler(asm_instance)

# --- process_db_pass1 のテスト ---

def test_db_numeric(handler):
    """DB命令（数値）のテスト"""
    item = {"line": 1, "asm": ["db", "10", ",", "0x20", ",", "255"]}
    assert handler.process_db_pass1(item) == [10, 0x20, 255]

def test_db_string(handler):
    """DB命令（文字列）のテスト"""
    item = {"line": 1, "asm": ["db", '"Hello"']}
    assert handler.process_db_pass1(item) == [ord('H'), ord('e'), ord('l'), ord('l'), ord('o')]

def test_db_mixed(handler):
    """DB命令（数値と文字列の混合）のテスト"""
    item = {"line": 1, "asm": ["db", '"A"', ",", "10", ",", "'B'"]}
    assert handler.process_db_pass1(item) == [ord('A'), 10, ord('B')]

def test_db_out_of_range_error(handler):
    """DB命令（範囲外の数値）のエラーテスト"""
    item = {"line": 1, "asm": ["db", "256"]}
    with pytest.raises(ValueError, match="DB value out of byte range"):
        handler.process_db_pass1(item)

def test_db_invalid_operand_error(handler):
    """DB命令（不正なオペランド）のエラーテスト"""
    item = {"line": 1, "asm": ["db", "INVALID"]}
    with pytest.raises(ValueError, match="Invalid operand for DB"):
        handler.process_db_pass1(item)

def test_db_invalid_string_literal_error(handler):
    """DB命令（不正な文字列リテラル）のエラーテスト"""
    item = {"line": 1, "asm": ["db", "'invalid string"]}
    with pytest.raises(ValueError, match="Invalid string literal"):
        handler.process_db_pass1(item)

# --- process_dw_pass1 のテスト ---

def test_dw_numeric(handler):
    """DW命令（数値）のテスト"""
    item = {"line": 1, "asm": ["dw", "0x1234", ",", "10"]}
    # リトルエンディアンで格納される
    assert handler.process_dw_pass1(item) == [0x34, 0x12, 0x0A, 0x00]

def test_dw_char_literals(handler):
    """DW命令（文字リテラル）のテスト"""
    item = {"line": 1, "asm": ["dw", "'A'", ",", "'BC'"]}
    # 'A' -> 0x0041 -> 41 00
    # 'BC' -> 0x4243 -> 43 42
    assert handler.process_dw_pass1(item) == [0x41, 0x00, 0x43, 0x42]

def test_dw_label(handler):
    """DW命令（ラベル）のテスト"""
    item = {"line": 1, "asm": ["dw", "MY_LABEL"]}
    # Pass 1では2バイトのプレースホルダーが生成される
    assert handler.process_dw_pass1(item) == [0x00, 0x00]

def test_dw_expression(handler):
    """DW命令（式）のテスト"""
    item = {"line": 1, "asm": ["dw", "MY_LABEL", "+", "1"]}
    # Pass 1では2バイトのプレースホルダーが生成される
    assert handler.process_dw_pass1(item) == [0x00, 0x00]

def test_dw_mixed(handler):
    """DW命令（混合オペランド）のテスト"""
    item = {"line": 1, "asm": ["dw", "0x1111", ",", "MY_LABEL", ",", "'C'"]}
    assert handler.process_dw_pass1(item) == [0x11, 0x11, 0x00, 0x00, 0x43, 0x00]

def test_dw_out_of_range_error(handler):
    """DW命令（範囲外の数値）のエラーテスト"""
    item = {"line": 1, "asm": ["dw", "65536"]}
    with pytest.raises(ValueError, match="DW value out of word range"):
        handler.process_dw_pass1(item)

def test_dw_long_string_literal_error(handler):
    """DW命令（3文字以上の文字列リテラル）のエラーテスト"""
    item = {"line": 1, "asm": ["dw", "'ABC'"]}
    with pytest.raises(ValueError, match="String literal in DW must be 1 or 2 characters"):
        handler.process_dw_pass1(item)

# --- process_dw_pass2 のテスト ---

def test_dw_pass2_label_resolution(handler):
    """DW命令（Pass 2, ラベル解決）のテスト"""
    # Asmクラスのメソッドをモック化
    handler.asm._evaluate_expression = Mock(return_value=(0x1234, 1))
    
    p = {
        "line": 1,
        "asm": ["dw", "MY_LABEL"],
        "opcode": [0x00, 0x00]  # Pass 1で生成されたプレースホルダー
    }
    label_map = {"MY_LABEL": 0x1234}
    
    handler.process_dw_pass2(p, label_map)
    
    # _evaluate_expressionが正しく呼ばれたか検証
    handler.asm._evaluate_expression.assert_called_with(p["asm"], 1, label_map, p["line"])
    
    # オペコードが正しく更新されたか検証 (リトルエンディアン)
    assert p["opcode"] == [0x34, 0x12]

def test_dw_pass2_undefined_label_error(handler):
    """DW命令（Pass 2, 未定義ラベル）のエラーテスト"""
    handler.asm._evaluate_expression = Mock(return_value=(None, 0))
    
    p = {"line": 1, "asm": ["dw", "UNDEFINED"], "opcode": [0x00, 0x00]}
    label_map = {}
    
    with pytest.raises(ValueError, match="Undefined label or invalid expression"):
        handler.process_dw_pass2(p, label_map)