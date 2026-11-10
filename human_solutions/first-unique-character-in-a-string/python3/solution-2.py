from collections import defaultdict

def first_unique_char(s):
    char_count = defaultdict(int)
    
    for char in s:
        char_count[char] += 1
    
    for i, char in enumerate(s):
        if char_count[char] == 1:
            return i
    
    return -1

# Test the function
input_str = "leetcode"
print(first_unique_char(input_str))  # Output: 0