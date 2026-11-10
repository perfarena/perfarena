def maximumUnits(boxTypes, truckSize):
    boxTypes.sort(key=lambda x: x[1], reverse=True)
    ans = 0
    for boxes, units in boxTypes:
        if boxes <= truckSize:
            ans += boxes * units
            truckSize -= boxes
        else:
            ans += truckSize * units
            break
    return ans