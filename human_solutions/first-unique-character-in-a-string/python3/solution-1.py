def first_non_unique_char(s):
    char_count = {}
    
    for i, c in enumerate(s):
        if c in char_count:
            char_count[c] += 1
        else:
            char_count[c] = 1
    
    for i, c in enumerate(s):
        if char_count[c] == 1:
            return i
    
    return -1  # If no non-unique character found

# Test the function
s = "hello"
result = first_non_unique_char(s)
print(result)  # Output: 0 (as 'h' is the first non-unique character at index 0)