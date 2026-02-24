def classify_number(value: int) -> str:
    if value < 0:
        return "negative"
    if value == 0:
        return "zero"
    if value < 10:
        return "small"
    if value < 100:
        return "medium"
    if value < 1000:
        return "large"
    if value < 10000:
        return "x-large"
    if value < 100000:
        return "xx-large"
    return "huge"
