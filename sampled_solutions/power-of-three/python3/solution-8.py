def is_power_of_3(n):
    if n <= 0:
        return False
    
    x = math.log10(n) / math.log10(3)
    
    return x == int(x)