def find_first_unique_char(s):
    memo = {}
    for i, char in enumerate(s):
        if char not in memo:
            memo[char] = i
        else:
            memo[char] = -1

    for item in memo:
        if memo[item] != -1:
            return memo[item]

    return -1

# Example usage
input_string = "lovely code"
result = find_first_unique_char(input_string)
print(result)  # Output: 2