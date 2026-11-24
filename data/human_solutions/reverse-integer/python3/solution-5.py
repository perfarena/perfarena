def reverse_integer(x):
    num = str(x)
    new_num = ''
    
    for i in range(len(num) - 1, -1, -1):
        new_num += num[i]
    
    reversed_int = int(new_num)
    
    if reversed_int >= -2**31 and reversed_int <= 2**31 - 1:
        return reversed_int if x >= 0 else -reversed_int
    else:
        return 0