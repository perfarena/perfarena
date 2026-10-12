def findHighestAltitude(gain):
    sum = 0
    max = 0
    
    for altitude in gain:
        sum += altitude
        if sum > max:
            max = sum
    
    return max