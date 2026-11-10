def is_integer(s):
    if not s:
        return False
    if s[0] in ['+', '-']:
        s = s[1:]
    return s.isdigit()

def is_decimal(s):
    if not s:
        return False
    if s[0] in ['+', '-']:
        s = s[1:]
    if '.' in s:
        s = s.replace('.', '')
    return s.isdigit()

def preprocess(s):
    s = s.lower()
    if 'e' in s:
        parts = s.split('e')
        part1 = parts[0]
        part2 = parts[1]
        return is_decimal(part1) and is_integer(part2)
    else:
        return is_integer(s) or is_decimal(s)

def is_valid_number(s):
    return preprocess(s)