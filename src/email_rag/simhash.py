from __future__ import annotations

import hashlib
import re

_TOKEN_RE = re.compile(r"[a-z0-9]+|[一-鿿]")


def tokenize(text: str) -> list[str]:
    """英文/数字按词切分，CJK 按单字切分。"""
    return _TOKEN_RE.findall(text.lower())


def _hash64(token: str) -> int:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def simhash64(tokens: list[str]) -> int:
    if not tokens:
        return 0
    weights = [0] * 64
    for token in tokens:
        h = _hash64(token)
        for i in range(64):
            weights[i] += 1 if (h >> i) & 1 else -1
    result = 0
    for i in range(64):
        if weights[i] > 0:
            result |= 1 << i
    return result


def hamming_distance(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def similarity(a: int, b: int) -> float:
    """相似度 = 1 - 汉明距离/64，范围 [0, 1]。"""
    return 1.0 - hamming_distance(a, b) / 64.0
