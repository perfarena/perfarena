def longest_substring_length(A):
    mp = {}
    answer = -1
    for i in range(len(A)):
        if A[i] in mp:
            answer = max(answer, i - mp[A[i]] - 1)
        else:
            mp[A[i]] = i
    return answer

# Test the function
A = "ABC"
result = longest_substring_length(A)
print(result)  # Output: 2