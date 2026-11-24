def climbStairs(n):
    if n == 1:
        return 1
    
    one, two = 1, 1
    
    for _ in range(n - 1):
        temp = one
        one = one + two
        two = temp
        
    return one

# Example usage
n = 5
ways_to_climb = climbStairs(n)
print(ways_to_climb)