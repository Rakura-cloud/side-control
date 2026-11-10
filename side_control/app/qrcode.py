# SPDX-License-Identifier: GPL-3.0-or-later
"""A minimal QR code encoder: byte mode, error correction M, versions 1 to 10.

That is all the Shortcuts guide needs (an address with a token is under 100 bytes, version 10 holds
213). ISO/IEC 18004 in a few lines: the bytes are bit-packed behind a mode and length header, every
block gets Reed-Solomon error correction, the blocks are interleaved, and the bits are laid out in a
matrix with finder, timing and alignment patterns. One of eight masks is applied so scanners can
tell the modules apart; the one with the lowest penalty score wins. No dependency needed for this.
"""

# (Reed-Solomon codewords per block, data codewords per block) of versions 1 to 10 at error
# correction M: the block table of ISO/IEC 18004. Group 1 comes first, so the interleaving matches.
BLOCKS = {1: (10, [16]), 2: (16, [28]), 3: (26, [44]), 4: (18, [32, 32]), 5: (24, [43, 43]),
          6: (16, [27, 27, 27, 27]), 7: (18, [31, 31, 31, 31]), 8: (22, [38, 38, 39, 39]),
          9: (22, [36, 36, 36, 37, 37]), 10: (26, [43, 43, 43, 43, 44])}
# Centres of the alignment patterns per version (none in version 1), from the same table
ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
         7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50]}
# The eight data masks, one lambda each (row, column). Column 6 is crossed with row 6 in some
# descriptions of mask 4 and 7; either spelling scores the same, this one is the common code.
MASKS = (lambda r, c: (r + c) % 2 == 0,
         lambda r, c: r % 2 == 0,
         lambda r, c: c % 3 == 0,
         lambda r, c: (r + c) % 3 == 0,
         lambda r, c: (r // 2 + c // 3) % 2 == 0,
         lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
         lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
         lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0)
GEN_POLY = 0x11D  # the Galois field GF(256) the error correction maths runs in
BCH_FORMAT = 0x537  # generator of the format info's BCH(15, 5) code...
FORMAT_MASK = 0x5412  # ...and the pattern its 15 bits are masked with
BCH_VERSION = 0x1F25  # generator of the version info's BCH(18, 6) code

_EXP, _LOG = [1] * 512, [0] * 256  # exp/log tables of GF(256), built once at import
_x = 1
for _i in range(255):
    _EXP[_i], _LOG[_x] = _x, _i
    _x = (_x << 1) ^ (GEN_POLY if _x & 0x80 else 0)
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mul(a, b):
    return 0 if not a or not b else _EXP[_LOG[a] + _LOG[b]]


def _remainder(codewords, degree):
    """The error correction codewords: the remainder of the message polynomial divided by the
    generator polynomial of `degree`, as synthetic division over GF(256)."""
    gen = [1]  # (x - a^0)(x - a^1)...: coefficients from the highest power down
    for i in range(degree):
        nxt = [0] * (len(gen) + 1)
        for j, factor in enumerate(gen):
            nxt[j] ^= factor
            nxt[j + 1] ^= _mul(factor, _EXP[i])
        gen = nxt
    out = list(codewords) + [0] * degree
    for i in range(len(codewords)):
        factor = out[i]  # as reduced by the steps before it
        for j in range(1, degree + 1):
            out[i + j] ^= _mul(gen[j], factor)
    return out[len(codewords):]


def _capacity(version):
    """How many bytes fit into this version, mode and length header included."""
    ec_per_block, blocks = BLOCKS[version]
    return (sum(blocks) * 8 - 4 - (8 if version < 10 else 16)) // 8


def _codewords(data, version):
    """The data codewords: header, bytes, terminator, then the 0xEC/0x11 padding."""
    bits = [0, 1, 0, 0]  # byte mode
    count = len(data)
    bits += [(count >> i) & 1 for i in range((8 if version < 10 else 16) - 1, -1, -1)]
    for byte in data:
        bits += [(byte >> i) & 1 for i in range(7, -1, -1)]
    total = sum(BLOCKS[version][1]) * 8
    bits += [0] * min(4, total - len(bits))
    bits += [0] * (-len(bits) % 8)
    out = [sum(bits[i + j] << (7 - j) for j in range(8)) for i in range(0, len(bits), 8)]
    out += [(0xEC, 0x11)[i % 2] for i in range(sum(BLOCKS[version][1]) - len(out))]
    return out


def _interleave(version, codewords):
    """Every block's data codewords column by column, then every block's error codewords."""
    ec_per_block, sizes = BLOCKS[version]
    blocks = [codewords[sum(sizes[:i]):sum(sizes[:i]) + size] for i, size in enumerate(sizes)]
    ec = [_remainder(block, ec_per_block) for block in blocks]
    out = [block[i] for i in range(max(sizes)) for block in blocks if i < len(block)]
    return out + [col for i in range(ec_per_block) for col in (e[i] for e in ec)]


def _patterns(grid, version):
    """Finder, separator, timing, alignment and version patterns, and the dark module; returns for
    every cell whether it is taken (and so is not data)."""
    size = len(grid)
    taken = [[False] * size for _ in range(size)]

    def paint(row, col, dark):
        grid[row][col], taken[row][col] = int(dark), True

    for row, col in ((0, 0), (0, size - 7), (size - 7, 0)):  # finders and their separators
        for r in range(-1, 8):
            for c in range(-1, 8):
                if 0 <= row + r < size and 0 <= col + c < size:
                    dark = 0 <= r <= 6 and 0 <= c <= 6 and max(abs(r - 3), abs(c - 3)) != 2
                    paint(row + r, col + c, dark)
    for row in ALIGN[version]:  # alignment patterns, leaving the finders alone (they win over the
        for col in ALIGN[version]:  # timing patterns, which are filled in after them)
            if taken[row][col]:
                continue
            for r in range(-2, 3):
                for c in range(-2, 3):
                    paint(row + r, col + c, max(abs(r), abs(c)) != 1)
    for i in range(8, size - 8):  # timing patterns
        if not taken[6][i]:
            paint(6, i, i % 2 == 0)
        if not taken[i][6]:
            paint(i, 6, i % 2 == 0)
    paint(size - 8, 8, True)  # the dark module
    for i in range(9):  # hold the format info's places (written after the mask is chosen)
        for row, col in ((i, 8), (8, i)):
            if not taken[row][col]:
                paint(row, col, False)
    for i in range(8):  # the second format copy: beside the top-right finder...
        paint(8, size - 1 - i, False)
    for i in range(7):  # ...and above the bottom-left one
        paint(size - 1 - i, 8, False)
    if version >= 7:  # hold the version info's places too
        for i in range(18):
            paint(i // 3, i % 3 + size - 11, False)
            paint(i % 3 + size - 11, i // 3, False)
    return taken


def _place(grid, taken, stream):
    """The codewords' bits, most significant first, in two-module columns from the bottom right,
    each column up or down as the last one ended."""
    size, bit, up = len(grid), 0, True
    col = size - 1
    while col > 0:
        if col == 6:
            col -= 1  # the timing column holds no data
        rows = range(size - 1, -1, -1) if up else range(size)
        for row in rows:
            for c in (col, col - 1):
                if not taken[row][c]:
                    byte = stream[bit >> 3] if bit >> 3 < len(stream) else 0
                    grid[row][c] = (byte >> (7 - (bit & 7))) & 1
                    bit += 1
        col -= 2
        up = not up


def _penalty(grid):
    """How badly this matrix scans: runs, blocks, finder-like patterns and the dark/light balance."""
    size = len(grid)
    lines = ["".join(str(v) for v in row) for row in grid]
    lines += ["".join(str(grid[r][c]) for r in range(size)) for c in range(size)]
    score = 0
    for line in lines:
        run = 1
        for i in range(1, size + 1):
            if i < size and line[i] == line[i - 1]:
                run += 1
            else:
                if run >= 5:
                    score += 3 + run - 5
                run = 1
        for i in range(size - 10):
            if line[i:i + 11] in ("10111010000", "00001011101"):
                score += 40
    score += 3 * sum(grid[r][c] == grid[r][c + 1] == grid[r + 1][c] == grid[r + 1][c + 1]
                     for r in range(size - 1) for c in range(size - 1))
    dark = sum(map(sum, grid))
    score += 10 * (abs(dark * 20 - size * size * 10) // (size * size))  # 10 per 5% off half dark
    return score


def _version_info(grid, version):
    """Versions 7 and up repeat their number in both far corners, as BCH(18, 6)."""
    rem = version << 12
    for shift in range(17, 11, -1):
        if rem >> shift:
            rem ^= BCH_VERSION << (shift - 12)
    bits = (version << 12) | rem
    size = len(grid)
    for i in range(18):
        dark = (bits >> i) & 1  # least significant bit first, like the format info
        grid[i // 3][i % 3 + size - 11] = dark
        grid[i % 3 + size - 11][i // 3] = dark


def _format(grid, mask):
    """The error correction level (M) and mask number, as BCH(15, 5), in both copies."""
    rem = mask << 10  # M is 00 in the format info's two level bits, so the five bits are the mask
    for shift in range(14, 9, -1):
        if rem >> shift:
            rem ^= BCH_FORMAT << (shift - 10)
    bits = ((mask << 10) | rem) ^ FORMAT_MASK
    size = len(grid)
    for i in range(15):
        dark = (bits >> i) & 1  # least significant bit first, per ISO/IEC 18004, Figure 25
        if i < 6:
            grid[i][8] = dark
        elif i < 8:
            grid[i + 1][8] = dark
        else:
            grid[size - 15 + i][8] = dark
        if i < 8:
            grid[8][size - i - 1] = dark
        elif i < 9:
            grid[8][7] = dark
        else:
            grid[8][15 - i - 1] = dark


def encode(text):
    """The QR matrix for `text`: rows of 0 (light) and 1 (dark) modules, quiet zone not included."""
    data = text.encode("utf-8")
    version = next((v for v in BLOCKS if len(data) <= _capacity(v)), None)
    if version is None:
        raise ValueError(f"{len(data)} bytes: too long, versions 1 to 10 hold at most 213")
    size = 17 + 4 * version
    grid = [[0] * size for _ in range(size)]
    taken = _patterns(grid, version)
    _place(grid, taken, _interleave(version, _codewords(data, version)))
    best, best_score = 0, None
    for mask, masker in enumerate(MASKS):
        for row in range(size):  # XOR to try the mask, XOR again to take it off
            for col in range(size):
                if not taken[row][col] and masker(row, col):
                    grid[row][col] ^= 1
        score = _penalty(grid)
        if best_score is None or score < best_score:
            best, best_score = mask, score
        for row in range(size):
            for col in range(size):
                if not taken[row][col] and masker(row, col):
                    grid[row][col] ^= 1
    for row in range(size):
        for col in range(size):
            if not taken[row][col] and MASKS[best](row, col):
                grid[row][col] ^= 1
    if version >= 7:
        _version_info(grid, version)
    _format(grid, best)
    return grid
