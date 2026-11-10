# Initialize the hashmap to store the last position of each character
last_occurrence = {char: idx for idx, char in enumerate(s)}
# Initialize stack and visited set
stack, visited = [], set()

# Iterate through the characters in the string
for i in range(len(s)):
    if s[i] in visited:
        continue
    while stack and stack[-1] > s[i] and last_occurrence[stack[-1]] > i:
        visited.remove(stack.pop())
    visited.add(s[i])
    stack.append(s[i])

# Concatenate the characters in the stack to get the final result
result = ''.join(stack)
return result