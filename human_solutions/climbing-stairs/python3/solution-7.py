def climbStairs(n):
    if n <= 2:
        return n
    
    first = 1
    second = 2
    
    for i in range(3, n+1):
        next_step = first + second
        first = second
        second = next_step
    
    return second