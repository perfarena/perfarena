def firstUniqChar(s: str) -> int:
    char_count = {}
    
    # Count occurrences of each character
    for char in s:
        char_count[char] = char_count.get(char, 0) + 1
    
    # Find the first non-repeating character
    for i, char in enumerate(s):
        if char_count[char] == 1:
            return i
    
    return -1

# Test the function
test_string = "loveleetcode"
print(firstUniqChar(test_string))  # Output: 2