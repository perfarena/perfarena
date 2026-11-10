# Sort the box types in descending order based on units per box
box_types.sort(key=lambda x: -x[1])

# Initialize variables
capacity = 0

# Iterate over box types
for boxes, units in box_types:
    if truck_size >= boxes:
        truck_size -= boxes
        capacity += boxes * units
    else:
        capacity += truck_size * units
        break

return capacity