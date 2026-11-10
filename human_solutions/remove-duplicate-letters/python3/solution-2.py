def remove_duplicate_letters(s):
    last_index = [-1] * 26
    seen = [False] * 26
    stack = []

    for i, char in enumerate(s):
        index = ord(char) - ord('a')
        last_index[index] = i

    for i, char in enumerate(s):
        index = ord(char) - ord('a')

        if seen[index]:
            continue

        while stack and char < stack[-1] and last_index[ord(stack[-1]) - ord('a')] > i:
            removed_char = stack.pop()
            seen[ord(removed_char) - ord('a')] = False

        stack.append(char)
        seen[index] = True

    return ''.join(stack)

# Test the function
test_string = "cbacdcbc"
result = remove_duplicate_letters(test_string)
print(result)  # Output: "acdb"