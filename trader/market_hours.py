"""US market hours, holidays and session clock."""

import datetime
from typing import List, Optional

from .config import ET_ZONE


# ==============================================================================
# 1. US Market Hours & Calendar Engine
# ==============================================================================

class MarketHours:
    """Accurate tracking of US Equities trading calendar and sessions."""

    @staticmethod
    def now_et() -> datetime.datetime:
        """Returns the current datetime in US Eastern Time."""
        return datetime.datetime.now(ET_ZONE)

    @staticmethod
    def now_local() -> datetime.datetime:
        """Returns the current datetime in the system local timezone (e.g. Mountain Time)."""
        return datetime.datetime.now().astimezone()

    @classmethod
    def is_weekend(cls, dt: Optional[datetime.datetime] = None) -> bool:
        dt = dt or cls.now_et()
        return dt.weekday() >= 5  # 5 = Saturday, 6 = Sunday

    @classmethod
    def get_easter(cls, year: int) -> datetime.date:
        """Computes Easter Sunday for a given year (Meeus/Jones/Butcher algorithm)."""
        a = year % 19
        b = year // 100
        c = year % 100
        d = b // 4
        e = b % 4
        f = (b + 8) // 25
        g = (b - f + 1) // 3
        h = (19 * a + b - d - g + 15) % 30
        i = c // 4
        k = c % 4
        l = (32 + 2 * e + 2 * i - h - k) % 7
        m = (a + 11 * h + 22 * l) // 451
        month = (h + l - 7 * m + 114) // 31
        day = ((h + l - 7 * m + 114) % 31) + 1
        return datetime.date(year, month, day)

    @classmethod
    def get_market_holidays(cls, year: int) -> List[datetime.date]:
        """Calculates observed US Stock Market (NYSE/NASDAQ) holidays for a given year."""
        holidays = []

        def add_observed(date_obj: datetime.date):
            if date_obj.weekday() == 6:  # Sunday -> observed Monday
                holidays.append(date_obj + datetime.timedelta(days=1))
            elif date_obj.weekday() == 5:  # Saturday -> observed Friday
                holidays.append(date_obj - datetime.timedelta(days=1))
            else:
                holidays.append(date_obj)

        # 1. New Year's Day (Jan 1)
        add_observed(datetime.date(year, 1, 1))

        # 2. Martin Luther King Jr. Day (3rd Monday in January)
        jan1 = datetime.date(year, 1, 1)
        first_mon = jan1 + datetime.timedelta(days=(0 - jan1.weekday() + 7) % 7)
        mlk = first_mon + datetime.timedelta(weeks=2)
        holidays.append(mlk)

        # 3. Washington's Birthday / Presidents Day (3rd Monday in February)
        feb1 = datetime.date(year, 2, 1)
        first_mon_feb = feb1 + datetime.timedelta(days=(0 - feb1.weekday() + 7) % 7)
        presidents_day = first_mon_feb + datetime.timedelta(weeks=2)
        holidays.append(presidents_day)

        # 4. Good Friday (Friday before Easter)
        easter = cls.get_easter(year)
        good_friday = easter - datetime.timedelta(days=2)
        holidays.append(good_friday)

        # 5. Memorial Day (Last Monday in May)
        may31 = datetime.date(year, 5, 31)
        memorial_day = may31 - datetime.timedelta(days=(may31.weekday() - 0) % 7)
        holidays.append(memorial_day)

        # 6. Juneteenth National Independence Day (June 19)
        add_observed(datetime.date(year, 6, 19))

        # 7. Independence Day (July 4)
        add_observed(datetime.date(year, 7, 4))

        # 8. Labor Day (1st Monday in September)
        sep1 = datetime.date(year, 9, 1)
        labor_day = sep1 + datetime.timedelta(days=(0 - sep1.weekday() + 7) % 7)
        holidays.append(labor_day)

        # 9. Thanksgiving Day (4th Thursday in November)
        nov1 = datetime.date(year, 11, 1)
        first_thu = nov1 + datetime.timedelta(days=(3 - nov1.weekday() + 7) % 7)
        thanksgiving = first_thu + datetime.timedelta(weeks=3)
        holidays.append(thanksgiving)

        # 10. Christmas Day (Dec 25)
        add_observed(datetime.date(year, 12, 25))

        return holidays

    @classmethod
    def is_market_holiday(cls, date_obj: Optional[datetime.date] = None) -> bool:
        """Returns True if the given date is a US stock market holiday."""
        if date_obj is None:
            date_obj = cls.now_et().date()
        holidays = cls.get_market_holidays(date_obj.year)
        return date_obj in holidays

    @classmethod
    def is_trading_day(cls, date_obj: Optional[datetime.date] = None) -> bool:
        """Returns True if the day is a valid trading day (weekday and not a holiday)."""
        if date_obj is None:
            date_obj = cls.now_et().date()
        if date_obj.weekday() >= 5:
            return False
        return not cls.is_market_holiday(date_obj)

    @classmethod
    def market_close_time(cls, date_obj: datetime.date) -> datetime.time:
        """Regular-session close in ET: 13:00 on NYSE early-close days, else 16:00.

        Early closes: the day after Thanksgiving, and Christmas Eve / July 3 when they are trading days."""
        if cls.is_trading_day(date_obj):
            nov1 = datetime.date(date_obj.year, 11, 1)
            thanksgiving = nov1 + datetime.timedelta(days=(3 - nov1.weekday() + 7) % 7, weeks=3)
            if date_obj == thanksgiving + datetime.timedelta(days=1):
                return datetime.time(13, 0)
            if (date_obj.month, date_obj.day) in ((12, 24), (7, 3)):
                return datetime.time(13, 0)
        return datetime.time(16, 0)

    @staticmethod
    def _to_et(dt: datetime.datetime) -> datetime.datetime:
        """Aware datetimes in any zone (e.g. Mountain Time) are converted to ET; naive ones are taken as ET."""
        return dt.astimezone(ET_ZONE) if dt.tzinfo is not None else dt.replace(tzinfo=ET_ZONE)

    @classmethod
    def get_market_session(cls, dt: Optional[datetime.datetime] = None) -> str:
        """
        Returns the current market session:
        - 'REGULAR': 09:30 - 16:00 ET (Regular Trading Hours)
        - 'PRE_MARKET': 04:00 - 09:30 ET
        - 'AFTER_HOURS': 16:00 - 20:00 ET
        - 'CLOSED': Nights, Weekends, Holidays
        """
        dt = cls._to_et(dt or cls.now_et())
        date_obj = dt.date()

        if not cls.is_trading_day(date_obj):
            return "CLOSED"

        current_time = dt.time()
        t_pre = datetime.time(4, 0)
        t_open = datetime.time(9, 30)
        t_close = cls.market_close_time(date_obj)
        t_post = datetime.time(17, 0) if t_close < datetime.time(16, 0) else datetime.time(20, 0)

        if t_open <= current_time < t_close:
            return "REGULAR"
        elif t_pre <= current_time < t_open:
            return "PRE_MARKET"
        elif t_close <= current_time < t_post:
            return "AFTER_HOURS"
        else:
            return "CLOSED"

    @classmethod
    def is_market_open(cls, dt: Optional[datetime.datetime] = None) -> bool:
        """Returns True if regular market hours are active (09:30 - 16:00 ET on a trading day)."""
        return cls.get_market_session(dt) == "REGULAR"

    @classmethod
    def next_market_open(cls, dt: Optional[datetime.datetime] = None) -> datetime.datetime:
        """Calculates the exact datetime of the next regular market open (9:30 AM ET)."""
        dt = dt or cls.now_et()
        target_date = dt.date()
        open_time = datetime.time(9, 30)

        # If today is a trading day and it's before 9:30 AM, today's open is next
        if cls.is_trading_day(target_date) and dt.time() < open_time:
            return datetime.datetime.combine(target_date, open_time, tzinfo=ET_ZONE)

        # Otherwise find the next trading day
        while True:
            target_date += datetime.timedelta(days=1)
            if cls.is_trading_day(target_date):
                return datetime.datetime.combine(target_date, open_time, tzinfo=ET_ZONE)

    @classmethod
    def seconds_until_next_open(cls, dt: Optional[datetime.datetime] = None) -> float:
        dt = dt or cls.now_et()
        next_open = cls.next_market_open(dt)
        return max(0.0, (next_open - dt).total_seconds())

    @classmethod
    def next_premarket_time(cls, dt: Optional[datetime.datetime] = None) -> datetime.datetime:
        """Calculates the exact datetime of the next pre-market briefing (9:20 AM ET - 10 min before open)."""
        dt = dt or cls.now_et()
        target_date = dt.date()
        pre_time = datetime.time(9, 20)

        # If today is a trading day and it's before 9:20 AM, today's pre-market is next
        if cls.is_trading_day(target_date) and dt.time() < pre_time:
            return datetime.datetime.combine(target_date, pre_time, tzinfo=ET_ZONE)

        # Otherwise find the next trading day
        while True:
            target_date += datetime.timedelta(days=1)
            if cls.is_trading_day(target_date):
                return datetime.datetime.combine(target_date, pre_time, tzinfo=ET_ZONE)

    @classmethod
    def seconds_until_premarket(cls, dt: Optional[datetime.datetime] = None) -> float:
        dt = dt or cls.now_et()
        next_pre = cls.next_premarket_time(dt)
        return max(0.0, (next_pre - dt).total_seconds())

    @classmethod
    def seconds_until_close(cls, dt: Optional[datetime.datetime] = None) -> float:
        dt = cls._to_et(dt or cls.now_et())
        if not cls.is_market_open(dt):
            return 0.0
        close_time = datetime.datetime.combine(dt.date(), cls.market_close_time(dt.date()), tzinfo=ET_ZONE)
        return max(0.0, (close_time - dt).total_seconds())

    @staticmethod
    def get_timezone(name: str):
        """Returns a timezone object for the given IANA timezone name. Falls back to ET_ZONE."""
        try:
            import zoneinfo
            return zoneinfo.ZoneInfo(name)
        except Exception:
            return ET_ZONE
