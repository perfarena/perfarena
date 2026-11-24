def find_max_digit(n):
    max_digit = 0
    for c in n:
        max_digit = max(max_digit, ord(c) - ord('0'))
    return max_digit