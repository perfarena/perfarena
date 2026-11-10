def preprocess(s):
    s = s.lower()
    if 'e' in s:
        parts = s.split('e')
        part1 = parts[0]
        part2 = parts[1]
        return is_decimal(part1) and is_integer(part2)
    else:
        return is_integer(s) or is_decimal(s)