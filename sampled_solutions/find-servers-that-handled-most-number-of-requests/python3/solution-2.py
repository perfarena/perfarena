def maximum_units(boxTypes, truckSize):
    # Sort the boxTypes based on the number of boxes in descending order
    boxTypes.sort(key=lambda x: x[1], reverse=True)
    
    total_units = 0
    for boxes, units_per_box in boxTypes:
        if truckSize >= boxes:
            total_units += boxes * units_per_box
            truckSize -= boxes
        else:
            total_units += truckSize * units_per_box
            break
    
    return total_units

# Example usage
boxTypes = [[5, 10], [2, 5], [4, 7], [3, 9]]
truckSize = 10
print(maximum_units(boxTypes, truckSize))  # Output: 91