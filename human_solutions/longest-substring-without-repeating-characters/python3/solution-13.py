def length_of_longest_substring(s):
    maxlen = 0
    start = 0
    d = {}
    
    for end in range(len(s)):
        if s[end] in d and d[s[end]] >= start:
            start = d[s[end]] + 1
        maxlen = max(maxlen, end - start + 1)
        d[s[end]] = end
    
    return maxlen

# Test the function
s = "abcabcbb"
print(length_of_longest_substring(s))  # Output: 3