#!/usr/bin/env python3

import os


def read_chunks(source):
    """バイナリファイルを読み込み、整数リストとして返す。

    Args:
        source (str | os.PathLike | list[tuple[str | os.PathLike, int]]):
            単一ファイルパス、または (ファイルパス, ロードアドレス) のリスト。
            リスト形式の場合、各ファイルを指定アドレスに配置した平坦なリストを返す。
            アドレス間のギャップは 0x00 で埋められる。

    Returns:
        list[int]: バイナリデータの整数リスト。

    Raises:
        FileNotFoundError: ファイルが存在しない場合。
        ValueError: アドレスが負の値の場合。

    Example:
        >>> data = read_chunks("rom.bin")
        >>> data = read_chunks([("prg0.bin", 0x0000), ("prg1.bin", 0x0800)])
    """
    if isinstance(source, (str, os.PathLike)):
        with open(source, "rb") as f:
            return list(f.read())

    images = []
    for path, addr in source:
        if addr < 0:
            raise ValueError(f"Address must be non-negative: {addr}")
        with open(path, "rb") as f:
            data = f.read()
        end = addr + len(data)
        if end > len(images):
            images += [0] * (end - len(images))
        images[addr:end] = list(data)
    return images


def write_chunks(dest, data):
    """整数リストをバイナリファイルに書き出す。

    Args:
        dest (str | os.PathLike | list[tuple[str | os.PathLike, int, int]]):
            単一ファイルパス、または (ファイルパス, 開始アドレス, 終了アドレス) のリスト。
            リスト形式の場合、data の指定範囲をそれぞれのファイルに書き出す。
            開始・終了アドレスは両端を含む (inclusive)。
        data (list[int]): 書き出すバイナリデータの整数リスト。

    Raises:
        ValueError: アドレスが負の値、または start > end の場合。

    Example:
        >>> write_chunks("output.bin", data)
        >>> write_chunks([("prg0.bin", 0x0000, 0x07FF), ("prg1.bin", 0x0800, 0x0FFF)], data)
    """
    if isinstance(dest, (str, os.PathLike)):
        with open(dest, "wb") as f:
            f.write(bytes(data))
        return

    for path, start, end in dest:
        if start < 0 or end < start:
            raise ValueError(f"Invalid address range: 0x{start:04X}-0x{end:04X}")
        with open(path, "wb") as f:
            f.write(bytes(data[start : end + 1]))
