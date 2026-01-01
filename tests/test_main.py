import os
import tempfile
import unittest
from argparse import Namespace

from pz80.__main__ import command_asm


class TestCommandAsm(unittest.TestCase):
    """command_asm関数のテストクラス"""

    def setUp(self):
        # テスト用の一時ファイルを作成
        self.asm_fd, self.asm_path = tempfile.mkstemp(suffix='.asm', text=True)
        self.bin_fd, self.bin_path = tempfile.mkstemp(suffix='.bin')
        # ファイルディスクリプタは閉じておく（パスで操作するため）
        os.close(self.asm_fd)
        os.close(self.bin_fd)

    def tearDown(self):
        # 一時ファイルを削除
        if os.path.exists(self.asm_path):
            os.remove(self.asm_path)
        if os.path.exists(self.bin_path):
            os.remove(self.bin_path)

    def _write_asm(self, content):
        """アセンブリソースを一時ファイルに書き込むヘルパー"""
        with open(self.asm_path, 'w', encoding='utf-8') as f:
            f.write(content)

    def test_basic_assemble(self):
        """基本的なアセンブルとバイナリ出力のテスト"""
        # nop (0x00) と ld a, 0x10 (0x3E, 0x10)
        self._write_asm("  nop\n  ld a, 0x10")
        
        args = Namespace(
            file=self.asm_path,
            output=self.bin_path,
            size=None
        )
        
        command_asm(args)
        
        with open(self.bin_path, 'rb') as f:
            data = f.read()
            
        self.assertEqual(data, b'\x00\x3E\x10')

    def test_org_padding(self):
        """ORG疑似命令によるアドレス指定とパディングのテスト"""
        # 0x0005番地に 0xAA を配置。0x0000-0x0004 は0埋めされることを期待
        self._write_asm("  org 0x0005\n  db 0xAA")
        
        args = Namespace(
            file=self.asm_path,
            output=self.bin_path,
            size=None
        )
        
        command_asm(args)
        
        with open(self.bin_path, 'rb') as f:
            data = f.read()
            
        expected = b'\x00\x00\x00\x00\x00\xAA'
        self.assertEqual(data, expected)

    def test_multiple_orgs(self):
        """複数のORGを使用した飛び地出力のテスト"""
        # 0x0000: 0x11
        # 0x0002: 0x22
        self._write_asm("  db 0x11\n  org 0x0002\n  db 0x22")
        
        args = Namespace(
            file=self.asm_path,
            output=self.bin_path,
            size=None
        )
        
        command_asm(args)
        
        with open(self.bin_path, 'rb') as f:
            data = f.read()
            
        # 0x00: 11, 0x01: 00(padding), 0x02: 22
        expected = b'\x11\x00\x22'
        self.assertEqual(data, expected)

    def test_size_option_decimal(self):
        """--size オプション（10進数）のテスト"""
        self._write_asm("  db 0xFF")
        
        # 出力サイズを10バイトに固定
        args = Namespace(
            file=self.asm_path,
            output=self.bin_path,
            size="10"
        )
        
        command_asm(args)
        
        with open(self.bin_path, 'rb') as f:
            data = f.read()
            
        self.assertEqual(len(data), 10)
        self.assertEqual(data[0], 0xFF)
        self.assertEqual(data[1:], b'\x00' * 9)

    def test_size_option_hex(self):
        """--size オプション（16進数）のテスト"""
        self._write_asm("  db 0xFF")
        
        # 出力サイズを0x10 (16) バイトに固定
        args = Namespace(
            file=self.asm_path,
            output=self.bin_path,
            size="0x10"
        )
        
        command_asm(args)
        
        with open(self.bin_path, 'rb') as f:
            data = f.read()
            
        self.assertEqual(len(data), 16)
        self.assertEqual(data[0], 0xFF)

if __name__ == '__main__':
    unittest.main()
