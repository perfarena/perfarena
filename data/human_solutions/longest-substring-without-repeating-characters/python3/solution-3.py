def length_of_longest_substring(s):
    char_index_map = {}
    start = 0
    max_length = 0

    for i, char in enumerate(s):
        if char in char_index_map and char_index_map[char] >= start:
            start = char_index_map[char] + 1

        char_index_map[char] = i
        max_length = max(max_length, i - start + 1)

    return max_length

# Test the function
input_string = "abcabcbb"
result = length_of_longest_substring(input_string)
print(result)  # Output: 3