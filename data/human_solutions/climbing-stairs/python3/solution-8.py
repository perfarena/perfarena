# Initialize base cases
if n == 1:
    return 1
if n == 2:
    return 2

# Initialize variables for the previous two steps
previous1 = 1
previous2 = 2

# Calculate the number of ways for each step starting from the third step
for i in range(3, n + 1):
    current = previous1 + previous2
    previous1, previous2 = previous2, current

# Return the result
return previous2