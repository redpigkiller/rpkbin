"""Reader-side budgets; not part of the V1 wire format."""

from dataclasses import dataclass, fields


@dataclass(frozen=True)
class DecodeLimits:
    """Allocation/metadata budgets. Raise individual limits for trusted large data.

    ``max_cells`` counts decoded column values, not bytes. These budgets reduce
    amplification; they are not a guarantee of an operating-system memory cap.
    """

    max_rows: int = 1_000_000
    max_cells: int = 10_000_000
    max_payload_bytes: int = 64 * 1024 * 1024
    max_schema_bytes: int = 1024 * 1024
    max_columns: int = 4096
    max_integer_bytes: int = 8192

    def __post_init__(self):
        for field in fields(self):
            value = getattr(self, field.name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{field.name} must be a positive int")


DEFAULT_LIMITS = DecodeLimits()
