def check_sequence(s, t):
    i = 0
    j = 0
    while i < len(t):
        if s[i] == t[j]:
            i += 1
        j += 1
        if i == len(t):
            return True
    return False