def largest_odd_number(num: str) -> str:
    i = len(num) - 1
    while i >= 0:
        if num[i] in {'1', '3', '5', '7', '9'}:
            return num[i:]
        i -= 1
    return ""