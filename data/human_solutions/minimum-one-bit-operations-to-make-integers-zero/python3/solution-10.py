def findHighestAltitude(gain):
    currentPoint = 0
    maxPoint = 0
    
    for gainUnit in gain:
        currentPoint += gainUnit
        maxPoint = max(maxPoint, currentPoint)
    
    return maxPoint