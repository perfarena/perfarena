def longest_substring(s):
    if not s:
        return 0
    
    i, j = 0, 0
    max_length = 0
    char_set = set()
    
    while i < len(s):
        c = s[i]
        if c in char_set:
            char_set.remove(s[j])
            j += 1
        else:
            char_set.add(c)
            max_length = max(max_length, i - j + 1)
            i += 1
    
    return max_length