def remove_adjacent_duplicates(input_string):
    stack = []
    
    for char in input_string:
        if stack and char == stack[-1]:
            stack.pop()
        else:
            stack.append(char)
    
    return ''.join(stack)

# Example usage
input_str = "abbaca"
output_str = remove_adjacent_duplicates(input_str)
print(output_str)  # Output: "ca"