# Initialize variables
p = properties
result = 0
stack = []

# Sort the properties array based on attack power increasingly and defense power decreasingly
p.sort(key=lambda x: (x[0], -x[1]))

# Iterate through each character's attack and defense properties
for a, d in p:
    # Check if the stack is not empty and the current character is weaker than the top element in the stack
    while stack and d > stack[-1]:
        stack.pop()
        result += 1
    # Push the current character's defense level to the stack
    stack.append(d)

# Return the total count of weak characters
return result