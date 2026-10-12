def length_of_longest_substring(s):
    start = 0
    max_length = 0
    char_set = set()

    for end in range(len(s)):
        while s[end] in char_set:
            char_set.remove(s[start])
            start += 1
        char_set.add(s[end])
        max_length = max(max_length, end - start + 1)

    return max_length

# Test the function
input_string = "abcabcbb"
result = length_of_longest_substring(input_string)
print(result)  # Output: 3