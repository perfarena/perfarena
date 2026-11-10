def max_substring_length(s):
    first_occurrence = [-1] * 26
    last_occurrence = [-1] * 26
    max_size = -1

    for i, c in enumerate(s):
        index = ord(c) - ord('a')
        if first_occurrence[index] == -1:
            first_occurrence[index] = i
        else:
            last_occurrence[index] = i
            size = last_occurrence[index] - first_occurrence[index] - 1
            max_size = max(max_size, size)

    return max_size if max_size >= 0 else 1

# Test the function
s = "abca"
print(max_substring_length(s))  # Output: 2