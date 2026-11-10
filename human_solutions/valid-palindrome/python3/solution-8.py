def is_palindrome(s):
    s = s.lower()
    s = ''.join([c for c in s if c.isalnum()])
    return s == s[::-1]