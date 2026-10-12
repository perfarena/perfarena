def climbStairs(n):
    if n <= 3:
        return n
    
    prev1, prev2, current = 3, 2, 0
    
    for i in range(3, n):
        current = prev1 + prev2
        prev2 = prev1
        prev1 = current
    
    return current