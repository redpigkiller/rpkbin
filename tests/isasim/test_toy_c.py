"""toy C：手算 golden、與獨立參考模型的差異測試、snapshot 往返的隨機測試。"""

import json
import random

import pytest

from rpkbin.isasim import Simulator, Snapshot

from .ref_toy_c import MEM_SIZE, Ref
from .toys.toy_c import make_mcu

# ---------------------------------------------------------------- 手算 golden
# 程式（12 位元 cell，位址 0 起）；ports = [3, 0]
C1 = {
    0: 0x794,  # WF 2, 5
    1: 0x30C,  # ADDI 12
    2: 0x207,  # ST [7]
    3: 0x800,
    4: 0x009,
    5: 0x880,  # STI [9], 17
    6: 0x510,  # IN 1
    7: 0x400,
    8: 0x020,  # JZ 2
    9: 0xFFF,  # 被 JZ 跳過
    11: 0xFFF,  # HLT
}
C1_GOLDEN = """#0 c=0 pc=0x00 [0x794] WF 2, 5 | w.H:0->2 w.L:0->5 | +1
#1 c=1 pc=0x01 [0x30c] ADDI 12 | w:0x15->0x01 mem[0x00]:0x00->0x01 | +1
#2 c=2 pc=0x02 [0x207] ST [7] | mem[0x07]:0x00->0x01 | +2
#3 c=4 pc=0x03 [0x800 0x009 0x880] STI [9], 17 | mem[0x09]:0x00->0x11 | +3
#4 c=7 pc=0x06 [0x510] IN 1 | w:0x01->0x00 | +1
#5 c=8 pc=0x07 [0x400 0x020] JZ 2 | - | +3
#6 c=11 pc=0x0b [0xfff] HLT | - | +1
= c=12 STOP halted: hlt
"""


def test_toy_c_hand_computed_golden(tmp_path):
    sim = Simulator(make_mcu())
    sim.load_cells("prog", C1)
    sim.poke("ports[0]", 3)
    path = tmp_path / "c1.log"
    sim.enable_trace(str(path), fmt="text")
    # poke 的 external event 在 enable_trace 之前發生，不會出現在檔案中
    result = sim.run(max_steps=100)
    sim.disable_trace()
    assert (
        result.stop_reason,
        result.steps,
        result.instructions,
        result.cycles,
        result.pc,
    ) == (
        "halted",
        7,
        7,
        12,
        12,
    )
    assert path.read_text() == C1_GOLDEN
    assert sim.read("w") == 0
    assert [sim.read(f"mem[{i}]") for i in (0, 7, 9)] == [1, 1, 17]


def test_toy_c_undefined_opcode_is_two_cell_nop(tmp_path):
    sim = Simulator(make_mcu())
    sim.load_cells(
        "prog", {0: 0x000, 1: 0xFFF}
    )  # 位址 0 未定義；nop 吃 2 個 cell，所以跳過 HLT
    events = []
    sim.subscribe(events.append)
    path = tmp_path / "u.log"
    sim.enable_trace(str(path), fmt="text")
    result = sim.step()
    sim.disable_trace()
    assert result.stop_reason == "step_done" and result.pc == 2 and result.cycle == 3
    instr = next(e for e in events if e.kind == "instr")
    assert instr.name == "<undefined>" and instr.cells == (0x000, 0xFFF)
    assert (
        path.read_text().splitlines()[0]
        == "#0 c=0 pc=0x00 [0x000 0xfff] <undefined> | - | +3"
    )
    assert [e.code for e in events if e.kind == "warning"] == ["undefined_opcode"]


def test_toy_c_fetch_edges():
    # 位址 99 是最後一個 cell：1 cell 指令可以執行，多 cell 指令需要位址 100 → out_of_range
    sim = Simulator(make_mcu())
    sim.load_cells("prog", {99: 0xFFF})
    sim.poke("pc", 99)
    assert sim.step().stop_reason == "halted"
    sim = Simulator(make_mcu())
    sim.load_cells("prog", {99: 0x400})  # JZ 的第一個 cell
    sim.poke("pc", 99)
    result = sim.step()
    assert result.stop_reason == "out_of_range"
    assert result.detail["space"] == "prog" and result.detail["addr"] == 100
    assert result.detail["access"] == "fetch" and result.steps == 0 and result.pc == 99


# ---------------------------------------------------------------- 隨機程式產生器
def _words(rng, length):
    """產生隨機程式（位址 -> 12 位元值），偏向產生合法指令，偶爾夾雜隨機字。"""

    def addr():
        return rng.randrange(20) if rng.random() < 0.85 else rng.randrange(256)

    prog = {}
    pc = 0
    while pc < length:
        kind = rng.choices(
            ["LD", "ST", "ADDI", "JZ", "IN", "JMP", "WF", "STI", "HLT", "RAW"],
            [10, 10, 12, 8, 6, 6, 8, 8, 2, 3],
        )[0]
        if kind == "LD":
            ws = [0x100 | addr()]
        elif kind == "ST":
            ws = [0x200 | addr()]
        elif kind == "ADDI":
            ws = [0x300 | (rng.randrange(4) << 6) | rng.randrange(64)]
        elif kind == "JZ":
            r = (
                rng.randrange(-6, 7) & 0xFF
                if rng.random() < 0.85
                else rng.randrange(256)
            )
            ws = [0x400, (r << 4) | rng.randrange(16)]
        elif kind == "IN":
            ws = [0x500 | (rng.randrange(2) << 4)]
        elif kind == "JMP":
            a = (
                rng.randrange(0, length + 6)
                if rng.random() < 0.9
                else rng.randrange(4096)
            )
            ws = [0x600 | ((a >> 8) << 4), ((a & 0xFF) << 4) | rng.randrange(16)]
        elif kind == "WF":
            ws = [0x700 | (rng.randrange(4) << 6) | (rng.randrange(8) << 2)]
        elif kind == "STI":
            a = rng.randrange(20) if rng.random() < 0.85 else rng.randrange(4096)
            ws = [0x800, a, (rng.randrange(32) << 7) | rng.randrange(128)]
        elif kind == "HLT":
            ws = [0xFFF]
        else:
            ws = [rng.randrange(4096)]
        for w in ws:
            prog[pc] = w
            pc += 1
    return prog


def _setup(rng, oor):
    prog = _words(rng, rng.randrange(8, 40))
    mem = [rng.randrange(32) for _ in range(MEM_SIZE)]
    ports = [rng.randrange(8), rng.randrange(8)]
    w = rng.randrange(32)
    pc = rng.randrange(0, 4)
    return prog, mem, ports, w, pc


def _build_sim(prog, mem, ports, w, pc, oor):
    sim = Simulator(make_mcu(oor=oor))
    sim.load_cells("prog", prog)
    for i, v in enumerate(mem):
        sim.poke(f"mem[{i}]", v)
    for i, v in enumerate(ports):
        sim.poke(f"ports[{i}]", v)
    sim.poke("w", w)
    sim.poke("pc", pc)
    return sim


def _dump(sim):
    d = {
        "pc": sim.read("pc"),
        "w": sim.read("w"),
        "cycle": sim.cycle,
        "steps": sim.steps_total,
    }
    for i in range(MEM_SIZE):
        d[f"mem{i}"] = sim.read(f"mem[{i}]")
    return d


def _ref_dump(ref):
    d = {"pc": ref.pc, "w": ref.w, "cycle": ref.cycle, "steps": ref.steps}
    for i in range(MEM_SIZE):
        d[f"mem{i}"] = ref.mem[i]
    return d


MAX_STEPS = 150


@pytest.mark.parametrize("oor", ["stop", "ignore"])
@pytest.mark.parametrize("seed", range(150))
def test_differential_against_reference(seed, oor):
    rng = random.Random(seed * 2 + (oor == "ignore"))
    prog, mem, ports, w, pc = _setup(rng, oor)
    sim = _build_sim(prog, mem, ports, w, pc, oor)
    ref = Ref(prog, oor=oor, mem=mem, ports=ports, w=w, pc=pc)
    events = []
    sim.subscribe(events.append)
    for _ in range(MAX_STEPS):
        events.clear()
        before = _dump(sim)
        out = ref.step()
        result = sim.step()
        assert result.stop_reason == (
            "step_done" if out.stop == "step_done" else out.stop
        ), (seed, oor)
        if out.stop == "out_of_range":
            # 中止型停止：狀態完全不變，只有一個 stop event
            assert _dump(sim) == before
            assert [e.kind for e in events] == ["stop"]
            for key, value in out.detail.items():
                assert result.detail[key] == value
            break
        assert _dump(sim) == _ref_dump(ref), (seed, oor, out.text)
        body = [e for e in events if e.kind != "stop"]
        warning_codes = {e.code for e in body if e.kind == "warning"}
        assert warning_codes == set(out.warns), (seed, oor, ref.fetch_out_of_range)
        if ref.fetch_out_of_range:
            assert "out_of_range" in warning_codes
            assert body[0].kind == "instr"
            assert all(e.kind == "warning" for e in body[1:])
        else:
            assert [e.kind for e in body] == ["instr"] + ["warning"] * len(out.warns)
            assert [e.code for e in body[1:]] == out.warns
        instr = body[0]
        assert instr.text == out.text
        if not ref.fetch_out_of_range:
            assert list(instr.cells) == out.cells
        assert instr.consumed == out.consumed
        got = [
            (w_.kind, w_.name, w_.addr, w_.old, w_.new, w_.bits, w_.is_field)
            for w_ in instr.writes
        ]
        assert got == out.writes, (seed, oor, out.text)
        if out.stop == "halted":
            break


def _run_all(sim, events):
    for _ in range(MAX_STEPS):
        result = sim.step()
        if result.stop_reason != "step_done":
            break


@pytest.mark.parametrize("seed", range(60))
def test_snapshot_roundtrip_at_random_step(seed, tmp_path):
    rng = random.Random(10_000 + seed)
    prog, mem, ports, w, pc = _setup(rng, "stop")
    k = rng.randrange(0, 30)

    # 一路跑完
    whole = _build_sim(prog, mem, ports, w, pc, "stop")
    whole_events = []
    whole.subscribe(whole_events.append)
    mark = None
    for i in range(MAX_STEPS):
        if i == k:
            mark = len(whole_events)
        result = whole.step()
        if result.stop_reason != "step_done":
            break
    if mark is None:  # 程式在第 k 步之前就結束了：把快照點移到結束後
        mark = len(whole_events)

    # 跑到第 k 步（或提前結束）→ snapshot（經 JSON 檔往返）→ 還原到全新的 Simulator → 繼續
    first = _build_sim(prog, mem, ports, w, pc, "stop")
    stopped = False
    for i in range(k):
        if first.step().stop_reason != "step_done":
            stopped = True
            break
    path = tmp_path / "snap.json"
    first.snapshot().save(str(path))
    json.loads(path.read_text())  # 必須是合法 JSON
    resumed = Simulator(make_mcu())
    resumed.restore(Snapshot.load(str(path)))
    resumed_events = []
    resumed.subscribe(resumed_events.append)
    if not stopped:
        for _ in range(MAX_STEPS - k):  # 與一路跑完的總步數上限相同
            if resumed.step().stop_reason != "step_done":
                break
    assert _dump(resumed) == _dump(whole)
    if not stopped:
        assert resumed_events == whole_events[mark:]
