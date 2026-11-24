def isGoodString(s):
    order_map = {}
    
    for char in s:
        if char in order_map:
            order_map[char] += 1
        else:
            order_map[char] = 1
    
    count = order_map[s[0]]
    
    for char in order_map:
        if order_map[char] != count:
            return False
    
    return True