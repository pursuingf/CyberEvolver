#!/usr/bin/env python3

import os
import sys
from capstone import *
from elftools.elf.elffile import ELFFile

def disassemble(path):
    """
    signature: disassemble '<path>'
    docstring: disassemble an ELF64 file and returns the pseudo C code.
    arguments:
        path(string, required): the path to the ELF64 file to be disassembled.
    """
    if not os.path.exists(path):
        raise ValueError("File not found: " + path)

    with open(path, "rb") as f:
        elf = ELFFile(f)
        text = elf.get_section_by_name(".text")
        code = text.data()
        addr = text["sh_addr"]

    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = True

    result = []
    result.append("/* ----- Pseudo C Output Starts----- */\n")

    for ins in md.disasm(code, addr):
        line = f"0x{ins.address:x}: {ins.mnemonic} {ins.op_str}"
        result.append(line)

    result.append("/* ----- Pseudo C Output  Ends----- */\n")

    return "\n".join(result)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: disassemble <path>")
        sys.exit(1)

    pseudo = disassemble(sys.argv[1])

    print(pseudo)

