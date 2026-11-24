def longest_substring_length(s):
    last_index = {}
    best_length = 0
    
    for index, char in enumerate(s):
        if char in last_index:
            length = index - last_index[char] - 1
            best_length = max(best_length, length)
        
        last_index[char] = index
    
    return best_length

# Test the function
s = "hey hey everybody this is Larry this is"
print(longest_substring_length(s))  # Output: 6