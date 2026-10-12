def findHighestAltitude(gain):
    sum_val = 0
    res = 0
    
    for i in range(len(gain)):
        sum_val += gain[i]
        res = max(res, sum_val)
    
    return res