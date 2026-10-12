def removeDuplicates(s: str) -> str:
    stack = []
    
    for char in s:
        if stack and stack[-1] == char:
            stack.pop()
        else:
            stack.append(char)
    
    return ''.join(stack)

# Example usage
result1 = removeDuplicates("abbaca")  # Output: "ca"
result2 = removeDuplicates("azxxzy")  # Output: "ay"
print(result1)
print(result2)