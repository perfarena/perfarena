def climbStairs(n):
    one = 1
    two = 1
    
    for _ in range(1, n):
        temp = one
        one = one + two
        two = temp
    
    return one

# Test the function with examples
print(climbStairs(2))  # Output: 2
print(climbStairs(3))  # Output: 3