from enum import StrEnum


class RemnawaveUserStatus(StrEnum):
    active = "ACTIVE"
    disabled = "DISABLED"
    limited = "LIMITED"
    expired = "EXPIRED"


class TrafficLimitStrategy(StrEnum):
    no_reset = "NO_RESET"
    day = "DAY"
    week = "WEEK"
    month = "MONTH"
    month_rolling = "MONTH_ROLLING"
