def maximum_units_on_truck(boxTypes, truckSize):
    # Sort the boxTypes based on the number of units in non-increasing order
    boxTypes.sort(key=lambda x: x[1], reverse=True)
    
    max_units = 0
    for boxes, units in boxTypes:
        if truckSize >= boxes:
            max_units += boxes * units
            truckSize -= boxes
        else:
            max_units += truckSize * units
            break
    
    return max_units

# Example usage
boxTypes = [[1, 3], [2, 2], [3, 1]]
truckSize = 4
print(maximum_units_on_truck(boxTypes, truckSize))  # Output: 8