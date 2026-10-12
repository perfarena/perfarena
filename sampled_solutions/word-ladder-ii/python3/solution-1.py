def one_character(s, t):
    if s == t:
        return False
    count = 0
    for i in range(len(s)):
        if s[i] != t[i]:
            count += 1
    return count == 1