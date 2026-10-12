# Initialize the stairs array
stairs = [0] * (n + 1)
stairs[0] = 1
stairs[1] = 1

# Calculate the number of ways to reach each step
for i in range(2, n + 1):
    stairs[i] = stairs[i - 1] + stairs[i - 2]

# Return the result
return stairs[n]