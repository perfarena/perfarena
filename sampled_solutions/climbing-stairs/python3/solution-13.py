def climbStairs(n):
    path = {1: 1, 2: 2, 3: 3}  # Known values
    for x in range(4, n + 1):
        path[x] = path[x - 1] + path[x - 2]  # Calculate ways for each step
    return path[n]  # Return the number of ways to reach step n

# Test the function with the target value (e.g., n = 4)
result = climbStairs(4)
print(result)  # Output should be 5