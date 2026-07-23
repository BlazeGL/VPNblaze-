from decimal import ROUND_HALF_UP, Decimal


class TrafficFormatter:
    UNITS = ("КБ", "МБ", "ГБ", "ТБ")

    @classmethod
    def bytes(cls, value: int) -> str:
        amount = Decimal(max(0, value)) / Decimal(1024)
        unit_index = 0
        while amount >= 1024 and unit_index < len(cls.UNITS) - 1:
            amount /= Decimal(1024)
            unit_index += 1
        rounded = amount.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        rendered = f"{rounded:f}".rstrip("0").rstrip(".")
        return f"{rendered} {cls.UNITS[unit_index]}"

    @classmethod
    def format(
        cls,
        used_bytes: int | None,
        total_bytes: int | None,
        *,
        unlimited: bool = False,
        unavailable: bool = False,
    ) -> str:
        if unavailable or used_bytes is None:
            return "Данные обновляются..."
        if unlimited or total_bytes == 0:
            return "Без ограничений"
        if total_bytes is None:
            return cls.bytes(used_bytes)
        return f"{cls.bytes(used_bytes)} / {cls.bytes(total_bytes)}"
