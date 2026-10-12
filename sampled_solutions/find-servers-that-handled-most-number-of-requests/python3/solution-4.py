def maximum_units(box_types, truck_size):
    # Sort box types by units per box in descending order
    box_types.sort(key=lambda x: x[1], reverse=True)
    
    total_units = 0
    for boxes, units_per_box in box_types:
        if truck_size >= boxes:
            total_units += boxes * units_per_box
            truck_size -= boxes
        else:
            total_units += truck_size * units_per_box
            break
    
    return total_units

# Example usage
box_types = [[1, 3], [2, 2], [3, 1]]  # Example box types with units per box
truck_size = 4  # Example truck size
max_total_units = maximum_units(box_types, truck_size)
print(max_total_units)  # Output: Maximum total units that can be put on the truck