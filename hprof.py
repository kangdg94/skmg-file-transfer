#!/usr/bin/env python3

import sys
import struct
from collections import Counter

U1 = struct.Struct(">B")
U2 = struct.Struct(">H")
U4 = struct.Struct(">I")
U8 = struct.Struct(">Q")

TYPE_SIZE = {
    2: None,   # object
    4: 1,      # boolean
    5: 2,      # char
    6: 4,      # float
    7: 8,      # double
    8: 1,      # byte
    9: 2,      # short
    10: 4,     # int
    11: 8,     # long
}

def read_exact(f, n):
    b = f.read(n)
    if len(b) != n:
        raise EOFError()
    return b

def u1(f):
    return U1.unpack(read_exact(f, 1))[0]

def u2(f):
    return U2.unpack(read_exact(f, 2))[0]

def u4(f):
    return U4.unpack(read_exact(f, 4))[0]

def u8(f):
    return U8.unpack(read_exact(f, 8))[0]

def rid(f, size):
    return int.from_bytes(read_exact(f, size), "big")

def skip_id(f, size, count=1):
    f.seek(size * count, 1)

def skip_value(f, type_code, id_size):
    size = id_size if type_code == 2 else TYPE_SIZE[type_code]
    f.seek(size, 1)

def header(f):
    buf = bytearray()

    while True:
        b = read_exact(f, 1)

        if b == b"\x00":
            break

        buf += b

    version = buf.decode("ascii", "replace")
    id_size = u4(f)
    timestamp = u8(f)

    return version, id_size, timestamp, f.tell()


def metadata_pass(path):

    strings = {}
    classes = {}

    with open(path, "rb") as f:

        version, id_size, timestamp, records_offset = header(f)

        while True:

            tag = f.read(1)

            if not tag:
                break

            tag = tag[0]

            u4(f)          # timestamp
            length = u4(f)

            start = f.tell()

            # STRING
            if tag == 0x01:

                sid = rid(f, id_size)

                value = read_exact(
                    f,
                    length - id_size
                ).decode("utf-8", "replace")

                strings[sid] = value

            # LOAD_CLASS
            elif tag == 0x02:

                u4(f)                         # class serial
                class_id = rid(f, id_size)
                u4(f)                         # stack trace serial
                name_id = rid(f, id_size)

                classes[class_id] = name_id

            else:
                f.seek(length, 1)

            f.seek(start + length)

    return (
        version,
        id_size,
        timestamp,
        records_offset,
        strings,
        classes
    )


def parse_heap(
    f,
    end,
    id_size,
    instance_count,
    instance_size,
    object_arrays,
    primitive_arrays
):

    while f.tell() < end:

        offset = f.tell()
        tag = u1(f)

        # GC ROOT
        if tag == 0xFF:
            skip_id(f, id_size)

        elif tag == 0x01:
            skip_id(f, id_size, 2)

        elif tag in (0x02, 0x03):
            skip_id(f, id_size)
            f.seek(8, 1)

        elif tag in (0x04, 0x06):
            skip_id(f, id_size)
            f.seek(4, 1)

        elif tag in (
            0x05, 0x07,
            0x89, 0x8A, 0x8B,
            0x8C, 0x8D, 0x90
        ):
            skip_id(f, id_size)

        elif tag == 0x08:
            skip_id(f, id_size)
            f.seek(8, 1)

        elif tag == 0x8E:
            skip_id(f, id_size)
            f.seek(8, 1)

        # CLASS_DUMP
        elif tag == 0x20:

            class_id = rid(f, id_size)

            u4(f)

            # super
            # loader
            # signers
            # protection domain
            # reserved
            # reserved
            skip_id(f, id_size, 6)

            size = u4(f)

            instance_size[class_id] = size

            # constant pool
            count = u2(f)

            for _ in range(count):
                f.seek(2, 1)

                t = u1(f)
                skip_value(f, t, id_size)

            # static fields
            count = u2(f)

            for _ in range(count):
                skip_id(f, id_size)

                t = u1(f)
                skip_value(f, t, id_size)

            # instance fields
            count = u2(f)

            for _ in range(count):
                skip_id(f, id_size)
                f.seek(1, 1)

        # INSTANCE_DUMP
        elif tag == 0x21:

            skip_id(f, id_size)

            u4(f)

            class_id = rid(f, id_size)

            data_length = u4(f)

            instance_count[class_id] += 1

            f.seek(data_length, 1)

        # OBJECT_ARRAY
        elif tag == 0x22:

            skip_id(f, id_size)

            u4(f)

            count = u4(f)

            class_id = rid(f, id_size)

            object_arrays[class_id] += count

            f.seek(count * id_size, 1)

        # PRIMITIVE_ARRAY
        elif tag == 0x23:

            skip_id(f, id_size)

            u4(f)

            count = u4(f)

            type_code = u1(f)

            size = TYPE_SIZE[type_code]

            primitive_arrays[type_code] += count * size

            f.seek(count * size, 1)

        # HEAP_DUMP_INFO
        elif tag == 0xFE:

            f.seek(4, 1)
            skip_id(f, id_size)

        else:

            raise RuntimeError(
                f"Unknown heap tag "
                f"0x{tag:02x} at offset 0x{offset:x}"
            )


def class_name(cid, classes, strings):

    name_id = classes.get(cid)

    if name_id is None:
        return f"<0x{cid:x}>"

    name = strings.get(
        name_id,
        f"<name 0x{name_id:x}>"
    )

    return name.replace("/", ".")


def human(n):

    for unit in ["B", "KB", "MB", "GB"]:

        if n < 1024:
            return f"{n:.1f}{unit}"

        n /= 1024

    return f"{n:.1f}TB"


def main():

    if len(sys.argv) != 2:
        print(
            f"Usage: {sys.argv[0]} file.hprof"
        )
        sys.exit(1)

    path = sys.argv[1]

    (
        version,
        id_size,
        timestamp,
        offset,
        strings,
        classes
    ) = metadata_pass(path)

    print("HPROF version :", version)
    print("Identifier    :", id_size, "bytes")
    print("Classes       :", f"{len(classes):,}")
    print("Strings       :", f"{len(strings):,}")
    print()

    instance_count = Counter()
    instance_size = {}
    object_arrays = Counter()
    primitive_arrays = Counter()

    with open(path, "rb") as f:

        f.seek(offset)

        while True:

            tag = f.read(1)

            if not tag:
                break

            tag = tag[0]

            u4(f)
            length = u4(f)

            end = f.tell() + length

            if tag in (
                0x0C,   # HEAP_DUMP
                0x1C    # HEAP_DUMP_SEGMENT
            ):

                parse_heap(
                    f,
                    end,
                    id_size,
                    instance_count,
                    instance_size,
                    object_arrays,
                    primitive_arrays
                )

            else:
                f.seek(length, 1)

    rows = []

    for cid, count in instance_count.items():

        size = instance_size.get(cid, 0)

        rows.append(
            (
                count * size,
                count,
                size,
                cid
            )
        )

    rows.sort(reverse=True)

    print("===== TOP CLASSES =====")

    print(
        f"{'objects':>15} "
        f"{'approx-size':>15} "
        f"class"
    )

    for total, count, size, cid in rows[:50]:

        print(
            f"{count:15,} "
            f"{human(total):>15} "
            f"{class_name(cid, classes, strings)}"
        )

    print()

    print("Total INSTANCE objects:",
          f"{sum(instance_count.values()):,}")


if __name__ == "__main__":
    main()