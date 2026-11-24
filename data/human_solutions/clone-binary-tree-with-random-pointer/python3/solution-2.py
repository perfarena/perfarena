def largest_substring_length(s):
    max_length = 0
    char_map = {}

    for i, char in enumerate(s):
        if char in char_map:
            max_length = max(max_length, i - char_map[char])
        else:
            char_map[char] = i

    return max_length

# Test the function with examples
print(largest_substring_length("ABBCA"))  # Output: 3
print(largest_substring_length("ABCDE"))  # Output: 0
print(largest_substring_length("ABeA"))   # Output: 0