def length_of_longest_substring(s):
    longest = 0
    L = 0
    chars_set = set()
    
    for R in range(len(s)):
        while s[R] in chars_set:
            chars_set.remove(s[L])
            L += 1
        
        chars_set.add(s[R])
        longest = max(longest, R - L + 1)
    
    return longest