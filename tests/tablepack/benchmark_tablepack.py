"""Reproducible acceptance/size check; external workbooks are strictly read-only.

Synthetic cases need only the normal project dependencies. Optional legacy XLS
reading uses xlrd, not a production TablePack dependency. No workbook contents
or encoded workbook blobs are saved; the report contains aggregate metrics.
"""

import argparse
import hashlib
import json
from pathlib import Path
import random
import struct
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from rpkbin.tablepack import infer, pack, unpack


def synthetic_cases():
    rng = random.Random(20261023)
    yield "joint_narrow", {f"c{j}": [rng.randrange(8) for _ in range(1000)] for j in range(8)}
    yield "long_runs", {"text": [f"long repeating register domain {i // 100 % 4}" for i in range(2000)],
                         "addr": [4096 + 4*i for i in range(2000)]}
    yield "wide128", {"reset": [rng.getrandbits(128) if i % 5 == 0 else 0 for i in range(1000)],
                       "optional": [None if i % 3 else i for i in range(1000)]}


def same_values(a, b):
    if type(a) is not type(b):
        return False
    return struct.pack(">d", a) == struct.pack(">d", b) if isinstance(a, float) else a == b


def measure(label, columns, compression, embedded):
    start = time.perf_counter()
    baseline = infer(columns, compress=compression, embed_schema=embedded, optimize=False)
    baseline_ms = (time.perf_counter()-start)*1000
    start = time.perf_counter()
    selected = infer(columns, compress=compression, embed_schema=embedded)
    selected_ms = (time.perf_counter()-start)*1000
    blobs = [pack(columns, s, embed_schema=embedded, compress=compression, verify=True)
             for s in (baseline, selected)]
    for blob, schema in zip(blobs, (baseline, selected)):
        back = unpack(blob, None if embedded else schema, as_columns=True)
        assert list(back) == list(columns)
        assert all(len(back[k]) == len(v) and all(same_values(a,b) for a,b in zip(v,back[k]))
                   for k,v in columns.items())
    assert len(blobs[1]) <= len(blobs[0]), "optimizer regressed against its own baseline"
    return {"case":label,"rows":len(next(iter(columns.values()))),"columns":len(columns),
            "compress":compression,"embedded":embedded,"baseline_bytes":len(blobs[0]),
            "optimized_bytes":len(blobs[1]),"baseline_infer_ms":baseline_ms,
            "optimized_infer_ms":selected_ms,"exact_roundtrip":True}


def typed_cells(sheet):
    """Split heterogeneous cells into typed values; preserve even empty vs text.

    Includes the header row as data. No guess about register addresses, merged
    values, formulas or hardware meanings is made. Styles and formula source
    are not part of this cell-value round-trip.
    """
    out = {}
    for j in range(sheet.ncols):
        cells = [sheet.cell(i,j) for i in range(sheet.nrows)]
        out[f"c{j}_kind"] = [c.ctype for c in cells]
        for typ, suffix in [(str,"text"),(float,"float"),(int,"int")]:
            if any(type(c.value) is typ for c in cells):
                out[f"c{j}_{suffix}"] = [c.value if type(c.value) is typ else None for c in cells]
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--excel-dir", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    results, sources = [], []
    for label, columns in synthetic_cases():
        for compression in ["zlib", "brotli", "lzma"]:
            for embedded in [False,True]:
                r = measure(label, columns, compression, embedded)
                results.append(r)
                print(json.dumps(r),flush=True)
    if args.excel_dir:
        import xlrd
        folder = args.excel_dir.resolve(strict=True)
        if args.output.resolve().is_relative_to(folder):
            raise ValueError("report output must be outside the read-only input directory")
        for ordinal, path in enumerate(sorted(folder.glob("*.xls")),1):
            if path.is_symlink() or path.resolve(strict=True).parent != folder:
                raise ValueError("input escapes the explicitly allowed directory")
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            workbook = xlrd.open_workbook(file_contents=raw, on_demand=True)
            try:
                for index in range(workbook.nsheets):
                    sheet = workbook.sheet_by_index(index)
                    if not sheet.nrows or not sheet.ncols:
                        continue
                    columns = typed_cells(sheet)
                    for embedded in [False,True]:
                        r = measure(f"workbook_{ordinal:03d}_sheet_{index}",columns,"brotli",embedded)
                        r["input_rows"],r["input_columns"] = sheet.nrows,sheet.ncols
                        results.append(r)
                        print(json.dumps(r),flush=True)
            finally:
                workbook.release_resources()
            if path.resolve(strict=True).parent != folder or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise RuntimeError("source workbook changed during acceptance")
            sources.append({"ordinal":ordinal,"sha256":digest,"unchanged":True})
    args.output.write_text(json.dumps({"results":results,"sources":sources},indent=2),encoding="utf-8")
    print(f"PASS: {len(results)} cases; {len(sources)} source workbooks unchanged.",flush=True)


if __name__ == "__main__":
    main()
