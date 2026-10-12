def check_equal_occurrences(input_string):
    char_freq = {}
    
    for char in input_string:
        if char in char_freq:
            char_freq[char] += 1
        else:
            char_freq[char] = 1
    
    if len(set(char_freq.values())) <= 1:
        return True
    else:
        return False

# Test the function
test_string = "FFFAAABBBCCC"
result = check_equal_occurrences(test_string)
print(result)