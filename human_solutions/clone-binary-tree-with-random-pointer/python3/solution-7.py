def longest_substring(s):
    locations = {}
    for index, value in enumerate(s):
        if value in locations:
            locations[value][1] = index
        else:
            locations[value] = [index, index]

    result = 0
    for start, end in locations.values():
        result = max(result, end - start - 1)

    return result if result > 0 else -1

# Test the function
s = "abcda"
print(longest_substring(s))  # Output: 3