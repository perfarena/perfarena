def length_of_longest_substring(s):
    L, R = 0, 0
    max_length = 0
    char_set = set()
    
    while R < len(s):
        if s[R] not in char_set:
            char_set.add(s[R])
            max_length = max(max_length, R - L + 1)
            R += 1
        else:
            char_set.remove(s[L])
            L += 1
    
    return max_length

# Example usage
s = "abcabcbb"
print(length_of_longest_substring(s))  # Output: 3