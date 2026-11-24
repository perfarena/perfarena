def climbStairs(n):
    if n <= 2:
        return n
    
    prev1, prev2 = 1, 2
    for i in range(2, n):
        current = prev1 + prev2
        prev1, prev2 = prev2, current
    
    return prev2

# Test the function
print(climbStairs(7))  # Output should be 21