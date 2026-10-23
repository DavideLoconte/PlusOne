#!/usr/bin/env python3
"""Disassemble an AArch64 function range and annotate adrp/add string refs."""
import re
import subprocess
import sys

lib, start, size = sys.argv[1], int(sys.argv[2], 16), int(sys.argv[3], 16)
data = open(lib, "rb").read()
# Map vaddr -> file offset through PT_LOAD segments.
segs = []
for line in subprocess.run(["readelf", "-lW", lib], capture_output=True, text=True).stdout.splitlines():
    f = line.split()
    if f and f[0] == "LOAD":
        segs.append((int(f[2], 16), int(f[1], 16), int(f[4], 16)))


def cstr(vaddr):
    for va, off, sz in segs:
        if va <= vaddr < va + sz:
            o = off + vaddr - va
            end = data.find(b"\0", o, o + 200)
            s = data[o:end if end != -1 else o + 200]
            if s and all(32 <= c < 127 or c in (9, 10) for c in s):
                return s.decode()
    return None


out = subprocess.run(["aarch64-linux-gnu-objdump", "-d", "--no-show-raw-insn", "-C",
                      f"--start-address={start:#x}", f"--stop-address={start + size:#x}", lib],
                     capture_output=True, text=True).stdout
pages = {}
for line in out.splitlines():
    m = re.search(r"adrp\s+(x\d+), ([0-9a-f]+)", line)
    if m:
        pages[m.group(1)] = int(m.group(2), 16)
    note = ""
    m = re.search(r"add\s+(x\d+), (x\d+), #0x([0-9a-f]+)", line)
    if m and m.group(2) in pages:
        s = cstr(pages[m.group(2)] + int(m.group(3), 16))
        if s:
            note = f"   ; \"{s[:120]}\""
    m = re.search(r"ldr\s+\w+, \[(x\d+), #(\d+)\]", line)
    if m and m.group(1) in pages and not note:
        pass
    if re.search(r"\b(bl|b\.|cbz|cbnz|tbz|tbnz|cmp|ret|strb|ldrb|str |mov\s+w\d+, #)", line) or note:
        print(line.strip()[:160] + note)
