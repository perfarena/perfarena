def findHighestAltitude(gain):
    current = 0
    highest = 0
    
    for altitude in gain:
        current += altitude
        highest = max(highest, current)
    
    return highest