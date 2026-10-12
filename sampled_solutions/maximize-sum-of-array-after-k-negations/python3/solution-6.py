def removeDuplicates(s):
    stack = []
    for char in s:
        if not stack:
            stack.append(char)
        elif char == stack[-1]:
            stack.pop()
        else:
            stack.append(char)
    return ''.join(stack)