def isPowerOfFour(n):
    if n <= 0 or n == -1:
        return False
    
    while n % 4 == 0:
        n /= 4
    
    return n == 1