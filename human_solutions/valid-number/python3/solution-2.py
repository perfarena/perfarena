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