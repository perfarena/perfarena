def reverse_integer(x):
    res = 0
    INT_MAX = 2**31 - 1
    INT_MIN = -2**31
    
    while x != 0:
        temp = x % 10
        x = x // 10
        
        if res > INT_MAX // 10 or (res == INT_MAX // 10 and temp > 7):
            return 0
        if res < INT_MIN // 10 or (res == INT_MIN // 10 and temp < -8):
            return 0
        
        res = res * 10 + temp
    
    return res