def is_palindrome(s):
    s = s.lower()
    s2 = ''.join([c for c in s if c.isalnum()])
    start, end = 0, len(s2) - 1
    while start < end:
        if s2[start] != s2[end]:
            return False
        start += 1
        end -= 1
    return True