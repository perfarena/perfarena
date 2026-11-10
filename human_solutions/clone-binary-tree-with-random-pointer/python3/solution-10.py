def longest_substring(s):
    max_length = -1
    for i in range(len(s) - 1, 0, -1):
        for j in range(i):
            if s[j] == s[i]:
                max_length = max(max_length, i - j - 1)
    return max_length

# Test the function
print(longest_substring("aa"))  # Output: 0
print(longest_substring("abc"))  # Output: 2