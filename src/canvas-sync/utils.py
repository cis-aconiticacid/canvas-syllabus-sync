import re
from datetime import datetime, timedelta


def normalize_course_code(raw_code: str) -> str:
    """
    将 Canvas 返回的 course_code 标准化，去掉学期和 section 后缀。

    Examples:
        "CS544_LEC_25F" → "CS544"
        "MATH521-001"   → "MATH521"

    Args:
        raw_code: Canvas 原始 course_code 字符串。

    Returns:
        标准化后的课程代码。
    """
    # 去掉 _LEC_、_DIS_、_LAB_ 等 section 后缀
    code = re.split(r'[_\-]\d', raw_code)[0]
    # 再去掉纯字母的学期标记（F / SP / SU + 两位年份）
    code = re.sub(r'[_\-]?((?:25|24|23)\w*)$', '', code)
    return code.strip('_- ')


def calc_week_number(published_at: str, week1_start: str, total_weeks: int) -> int | None:
    """
    根据文件发布时间计算所属 week 编号。

    Args:
        published_at:  文件发布时间，ISO 8601 字符串。
        week1_start:   学期第一周开始日期，格式 "YYYY-MM-DD"。
        total_weeks:   学期总周数。

    Returns:
        week 编号（1 到 total_weeks），超出范围或解析失败返回 None。
    """
    try:
        pub = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        w1 = datetime.fromisoformat(week1_start)
        # 统一去掉 timezone 做差值
        pub_naive = pub.replace(tzinfo=None)
        delta_days = (pub_naive - w1).days
        if delta_days < 0:
            return None
        week_num = delta_days // 7 + 1
        if week_num > total_weeks:
            return None
        return week_num
    except Exception:
        return None


def now_iso() -> str:
    """
    返回当前 UTC 时间的 ISO 8601 字符串，用于写入数据库的时间戳字段。

    Returns:
        格式如 "2025-09-03T10:00:00.000000"
    """
    return datetime.utcnow().isoformat()
