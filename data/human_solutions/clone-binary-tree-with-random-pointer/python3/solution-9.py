def longest_substring(s):
    char_index_map = {}
    result = -1
    
    for i, char in enumerate(s):
        if char in char_index_map:
            result = max(result, i - char_index_map[char] - 1)
        else:
            char_index_map[char] = i
    
    return result

# Test the function
input_string = "abcdeabc"
print(longest_substring(input_string))  # Output should be 5