def longest_substring(s):
    answer = -1
    size = len(s)
    
    for i in range(size - 1):
        for j in range(i + 1, size):
            if s[i] == s[j]:
                answer = max(answer, j - i - 1)
    
    return answer

# Test the function
s = "leetcode"
print(longest_substring(s))  # Output: 3