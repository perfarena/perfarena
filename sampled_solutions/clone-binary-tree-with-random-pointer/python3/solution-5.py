def longest_substring(s):
    D = {}
    result = -1
    
    for i, char in enumerate(s):
        if char not in D:
            D[char] = i
    
    for i in range(len(s) - 1, -1, -1):
        start = D.get(s[i], i)
        result = max(result, i - start - 1)
    
    return result

# Test the function
input_str = "abacabad"
print(longest_substring(input_str))  # Output: 3