import os
import sys

import pytest

# srcディレクトリをパスに追加（パッケージ未インストール時の開発用）
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from pz80.asm import Asm
from pz80.disasm import Disasm


@pytest.fixture
def assembler():
    return Asm()

@pytest.fixture
def disassembler():
    return Disasm()