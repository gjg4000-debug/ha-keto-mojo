"""VivaChek (Keto-Mojo GK+) frame protocol.

Commands are fixed hex frames written to the cdd2 characteristic; replies
arrive as notifications on cdd1. Frames are 7B ... 7D.

Each record frame carries 9 payload bytes at hex offset 18..36:
  yy mo dd hh mi  v_hi v_lo  flags  type
value = v_hi * 100 + v_lo; ketone types are hundredths of mmol/L, glucose is
mg/dL (or tenths of mmol/L when the meter is set to mmol).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

CMD_GET_UNIT = "7B01100120AA55000002010D087D"
CMD_GET_SERIAL = "7B0110012077550000010B0B047D"
CMD_FETCH_ALL = "7B01100120DD550000030A060C7D"

END_OF_RECORDS = "7B01200110D16600000B0903047D"
UNIT_REPLY_PREFIX = "7B01200110AAAA0001"
SERIAL_REPLY_PREFIX = "7B0120011077AA0010"

UNIT_MG_DL = "mg/dL"
UNIT_MMOL = "mmol/L"

GLUCOSE = "glucose"
KETONE = "ketone"
GLUCOSE_QC = "glucose_control"
KETONE_QC = "ketone_control"

# type byte -> (kind, part of a GKI pair)
_TYPES = {
    "11": (GLUCOSE, False),
    "12": (GLUCOSE, True),
    "55": (KETONE, False),
    "56": (KETONE, True),
    "22": (GLUCOSE_QC, False),
    "66": (KETONE_QC, False),
}

MG_DL_PER_MMOL = 18


@dataclass(frozen=True)
class Reading:
    when: datetime  # naive, meter-local time
    kind: str
    value: float
    unit: str
    gki_pair: bool
    meal: str  # "general", "pre" or "post"

    @property
    def key(self) -> str:
        return f"{self.when.isoformat()}|{self.kind}|{self.value}"


def parse_unit(hexbuf: str) -> str | None:
    idx = hexbuf.find(UNIT_REPLY_PREFIX)
    if idx < 0:
        return None
    code = hexbuf[idx + len(UNIT_REPLY_PREFIX) : idx + len(UNIT_REPLY_PREFIX) + 2]
    return {"11": UNIT_MG_DL, "22": UNIT_MMOL}.get(code)


def parse_serial(hexbuf: str) -> str | None:
    idx = hexbuf.find(SERIAL_REPLY_PREFIX)
    if idx < 0:
        return None
    raw = hexbuf[idx + len(SERIAL_REPLY_PREFIX) : idx + len(SERIAL_REPLY_PREFIX) + 32]
    try:
        return bytes.fromhex(raw).decode("ascii").strip("\x00 ")
    except ValueError:
        return None


def parse_records(hexbuf: str, glucose_unit: str) -> list[Reading]:
    readings: list[Reading] = []
    for frame in hexbuf.upper().split("7D"):
        if len(frame) <= 36 or not frame.startswith("7B"):
            continue
        payload = frame[18:36]
        b = [int(payload[i : i + 2], 16) for i in range(0, 18, 2)]
        kind_info = _TYPES.get(payload[16:18])
        if kind_info is None:
            continue
        kind, gki_pair = kind_info
        try:
            when = datetime(2000 + b[0], b[1], b[2], b[3], b[4])
        except ValueError:
            continue
        raw = b[5] * 100 + b[6]
        if kind in (KETONE, KETONE_QC):
            value, unit = raw / 100, UNIT_MMOL
        elif glucose_unit == UNIT_MMOL:
            value, unit = raw / 10, UNIT_MMOL
        else:
            value, unit = float(raw), UNIT_MG_DL
        meal = {"1": "pre", "2": "post"}.get(payload[14], "general")
        readings.append(Reading(when, kind, value, unit, gki_pair, meal))
    return readings


def glucose_mmol(reading: Reading) -> float:
    return reading.value if reading.unit == UNIT_MMOL else reading.value / MG_DL_PER_MMOL


def compute_gki(glucose: Reading, ketone: Reading) -> float | None:
    """Glucose-Ketone Index, floored to 0.1 like the Keto-Mojo app."""
    if ketone.value <= 0:
        return None
    return int(glucose_mmol(glucose) / ketone.value * 10) / 10


GKI_TIME_WINDOW_S = 30 * 60


def pair_gki(readings: list[Reading]) -> list[tuple[Reading, Reading, float, str]]:
    """Pair glucose and ketone readings into GKI values.

    First the meter's own GKI-mode pairs (flagged records sitting next to each
    other), then any remaining glucose + ketone taken within 30 minutes of each
    other. Each result is (glucose, ketone, gki, "meter" | "time").
    """
    pairs = []
    used: set[int] = set()
    for i, r in enumerate(readings):
        if r.kind != KETONE or not r.gki_pair:
            continue
        for j in (i - 1, i + 1):
            if 0 <= j < len(readings) and j not in used:
                g = readings[j]
                if g.kind == GLUCOSE and g.gki_pair:
                    gki = compute_gki(g, r)
                    if gki is not None:
                        pairs.append((g, r, gki, "meter"))
                        used.update((i, j))
                    break

    for i, k in enumerate(readings):
        if k.kind != KETONE or i in used:
            continue
        best = None
        for j, g in enumerate(readings):
            if g.kind != GLUCOSE or j in used:
                continue
            gap = abs((g.when - k.when).total_seconds())
            if gap <= GKI_TIME_WINDOW_S and (best is None or gap < best[0]):
                best = (gap, j)
        if best is not None:
            g = readings[best[1]]
            gki = compute_gki(g, k)
            if gki is not None:
                pairs.append((g, k, gki, "time"))
                used.update((i, best[1]))
    return pairs
