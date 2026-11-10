def length_of_longest_substring(s):
    start = 0
    max_length = 0
    lookup = {}

    for i, char in enumerate(s):
        if char in lookup:
            start = max(start, lookup[char] + 1)
        
        max_length = max(max_length, i - start + 1)
        lookup[char] = i

    return max_length

# Test the function
input_string = "ABCABCBB"
result = length_of_longest_substring(input_string)
print(result)  # Output should be 3