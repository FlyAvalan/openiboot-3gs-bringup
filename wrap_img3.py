#!/usr/bin/env python3
# Wrap a raw openiboot .bin into an img3 by swapping ONLY the DATA tag of the
# reference signed img3 (kloader ignores SHSH/CERT). Usage: wrap_img3.py <in.bin> <out.img3>
import struct, sys
REF = '/Users/flypathic/Downloads/openiboot-3gs/iphone_3gs_openiboot.img3'
inbin, outimg = sys.argv[1], sys.argv[2]
orig = open(REF, 'rb').read()
newbin = open(inbin, 'rb').read()
magic, fullSize, dataSize, shshOff, ident = struct.unpack_from('<4sIII4s', orig, 0)
tags = []
off = 20
while off < len(orig) - 8:
    tmagic, total, dlen = struct.unpack_from('<4sII', orig, off)
    if total == 0:
        break
    tags.append((tmagic, total, dlen, off))
    off += total
name = lambda m: m[::-1].decode(errors='replace')
out = bytearray()
shsh_file_off = None
for tmagic, total, dlen, toff in tags:
    if name(tmagic) == 'DATA':
        data = newbin + b'\x00' * ((-len(newbin)) % 4)
        out += struct.pack('<4sII', tmagic, 12 + len(data), len(newbin)) + data
    else:
        if name(tmagic) == 'SHSH' and shsh_file_off is None:
            shsh_file_off = 20 + len(out)
        out += orig[toff:toff + total]
full2 = 20 + len(out)
shsh2 = (shsh_file_off - 20) if shsh_file_off else shshOff
final = struct.pack('<4sIII4s', magic, full2, full2 - 20, shsh2, ident) + bytes(out)
open(outimg, 'wb').write(final)
i = final.find(b'ATAD'); t, tot, dl = struct.unpack_from('<4sII', final, i)
assert final[i+12:i+12+dl] == newbin, "DATA payload mismatch!"
print("wrote %s: %d bytes, DATA=0x%x ok" % (outimg, len(final), dl))
