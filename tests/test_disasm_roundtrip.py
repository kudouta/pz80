import os
import tempfile
import unittest

from pz80 import asm, disasm


class TestDisasm(unittest.TestCase):
    def setUp(self):
        self.asm = asm.Asm()
        self.disasm = disasm.Disasm()

    def _assemble_and_disassemble(self, source, start_addr=0):
        """
        ソースコードをアセンブルし、その結果を逆アセンブルして結果のリストを返すヘルパー
        """
        # 1. アセンブル実行
        with tempfile.NamedTemporaryFile(mode='w+', delete=False, encoding='utf-8', suffix='.asm') as tmp:
            tmp.write(source)
            tmp_path = tmp.name
        
        try:
            asm_result = self.asm.exec(tmp_path)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

        # 2. バイナリイメージ(メモリ)の構築
        # disasm.exec は 64KB のリストを要求するため
        memory = [0] * 0x10000
        max_addr = 0
        
        # アセンブル結果からメモリに配置
        for line in asm_result:
            if "opcode" in line and line["opcode"]:
                addr = line["base"] + line["offset"]
                code = line["opcode"]
                for i, byte in enumerate(code):
                    if addr + i < 0x10000:
                        memory[addr + i] = byte
                
                current_end = addr + len(code)
                if current_end > max_addr:
                    max_addr = current_end

        if max_addr == 0:
            return []

        size = max_addr - start_addr
        
        # 3. 逆アセンブル実行
        # disasm.exec は images[0] を start_addr に配置するため、
        # memory全体ではなく、start_addr からのデータを切り出して渡す必要がある
        binary_chunk = memory[start_addr : start_addr + size]
        disasm_result = self.disasm.exec(start_addr, binary_chunk, size)
        
        return disasm_result

    def test_basic_instructions(self):
        """基本命令の逆アセンブルテスト"""
        source = """
            ORG 0x1000
            NOP
            LD A, 0x55
            HALT
        """
        result = self._assemble_and_disassemble(source, 0x1000)
        
        # 結果の検証 (asm文字列が含まれているか)
        # disasm.py はオペランドを小文字(z80.py定義)で出力し、数値は 0xXX 形式
        asm_lines = [r["asm"] for r in result if "asm" in r]
        
        self.assertTrue(any("NOP" in line for line in asm_lines))
        # z80.py定義: ["ld", "a", ",", "0x{0}"] -> "LD a, 0x55"
        self.assertTrue(any("LD a, 0x55" in line for line in asm_lines))
        self.assertTrue(any("HALT" in line for line in asm_lines))

    def test_relative_jump_label(self):
        """相対ジャンプとラベル解決のテスト"""
        source = """
            ORG 0x2000
            JR TARGET
            NOP
TARGET:     LD B, 0x10
        """
        # 0x2000: JR 0x01 (ターゲット=0x2003), 2バイト
        # 0x2002: NOP
        # 0x2003: LD B, 0x10
        
        result = self._assemble_and_disassemble(source, 0x2000)
        
        # JR命令が "JR L_2003" のように解決されているか確認
        jr_line = next((r for r in result if "JR" in r.get("asm", "")), None)
        self.assertIsNotNone(jr_line)
        self.assertIn("L_2003", jr_line["asm"])

    def test_ix_instruction(self):
        """IX命令(4バイト)のテスト"""
        source = """
            ORG 0x3000
            LD (IX+5), 0xAA
        """
        result = self._assemble_and_disassemble(source, 0x3000)
        
        ix_line = next((r for r in result if "ix" in r.get("asm", "").lower()), None)
        self.assertIsNotNone(ix_line)
        # 期待: LD (ix+0x05), 0xAA
        # z80.py定義: ["ld", "(", "ix", "+", "0x{0}", ")", ",", "0x{1}"]
        # disasm.py: replace(",", ", ") -> "LD (ix+0x05), 0xAA"
        self.assertIn("(ix+0x05), 0xAA", ix_line["asm"])

if __name__ == '__main__':
    unittest.main()