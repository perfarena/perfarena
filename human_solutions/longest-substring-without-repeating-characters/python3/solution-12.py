def longest_substring(s):
    char_dict = {}
    left = 0
    right = 0
    max_length = 0
    
    while right < len(s):
        char = s[right]
        if char in char_dict:
            left = max(left, char_dict[char] + 1)
        
        char_dict[char] = right
        max_length = max(max_length, right - left + 1)
        right += 1
    
    return max_length

# Example usage
input_string = "abcabcbb"
result = longest_substring(input_string)
print(result)  # Output: 3