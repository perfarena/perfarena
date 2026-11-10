def maximumUnits(boxTabs, truckSize):
    boxTabs.sort(key=lambda x: x[1], reverse=True)  # Sort in descending order of units per box
    result = 0
    boxes_on_truck = 0

    for boxes, units_per_box in boxTabs:
        if boxes_on_truck + boxes <= truckSize:
            result += boxes * units_per_box
            boxes_on_truck += boxes
        else:
            remaining_space = truckSize - boxes_on_truck
            result += remaining_space * units_per_box
            break

    return result

# Example usage
boxTabs = [[5, 9], [3, 7], [2, 8]]
truckSize = 10
print(maximumUnits(boxTabs, truckSize))  # Output: 91