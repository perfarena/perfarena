def climbStairs(n):
    if n == 1:
        return 1
    if n == 2:
        return 2
    
    one_step = 1
    two_step = 2
    
    for i in range(3, n+1):
        temp = one_step + two_step
        one_step = two_step
        two_step = temp
    
    return two_step

# Example usage
n = 6
distinct_ways = climbStairs(n)
print(f"Number of distinct ways to climb {n} steps: {distinct_ways}")