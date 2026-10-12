from collections import defaultdict

def first_unique_char_index(s):
    char_count = defaultdict(int)
    
    for char in s:
        char_count[char] += 1
    
    for i, char in enumerate(s):
        if char_count[char] == 1:
            return i
    
    return -1

# Test the implementation
print(first_unique_char_index("leetcode"))  # Output: 0
print(first_unique_char_index("loveleetcode"))  # Output: 2
print(first_unique_char_index("aabb"))  # Output: -1