def first_unique_char(s):
    freq = {}  # Dictionary to store character frequencies
    indexes = {}  # Dictionary to store character indexes
    for i, char in enumerate(s):
        freq[char] = freq.get(char, 0) + 1
        if char not in indexes:
            indexes[char] = i

    unique_chars = [char for char, count in freq.items() if count == 1]

    if not unique_chars:
        return -1

    min_index = float('inf')
    for char in unique_chars:
        min_index = min(min_index, indexes[char])

    return min_index

# Test cases
print(first_unique_char("leetcode"))  # Output: 0
print(first_unique_char("loveleetcode"))  # Output: 2
print(first_unique_char("aabbcc"))  # Output: -1