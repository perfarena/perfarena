def longest_substring(s):
    char_map = {}
    answer = -1
    
    for i, char in enumerate(s):
        if char in char_map:
            length = i - char_map[char] - 1
            answer = max(answer, length)
        else:
            char_map[char] = i
    
    return answer

# Test the function
s = "abacabad"
result = longest_substring(s)
print(result)  # Output: 4