def remove_adjacent_duplicates(s):
    stack = []
    for char in s:
        if stack and char == stack[-1]:
            stack.pop()
        else:
            stack.append(char)
    return ''.join(stack)

# Test the function
input_string = "bbaac"
result = remove_adjacent_duplicates(input_string)
print(result)