# Sort the 2D array based on the number of units per box in decreasing order
boxType.sort(key=lambda x: x[1], reverse=True)

maxLoadedBoxCount = 0
for box in boxType:
    numBoxes = min(box[0], truckSize)
    maxLoadedBoxCount += numBoxes * box[1]
    truckSize -= numBoxes
    if truckSize == 0:
        return maxLoadedBoxCount

return maxLoadedBoxCount