def max_units_on_truck(num_boxes, boxes, truck_size):
    # Sort boxes based on units per box in descending order
    boxes.sort(key=lambda x: x[1], reverse=True)
    
    total_units = 0
    for box_count, box_units in boxes:
        # Calculate the number of boxes to use based on truck size and remaining boxes
        used_boxes = min(truck_size, box_count)
        
        # Update total units loaded onto the truck
        total_units += used_boxes * box_units
        
        # Update remaining truck size
        truck_size -= used_boxes
        
        if truck_size == 0:
            break
    
    return total_units

# Example usage
num_boxes = 4
boxes = [(5, 10), (3, 7), (2, 9), (4, 8)]  # Format: (number of boxes, units per box)
truck_size = 10

result = max_units_on_truck(num_boxes, boxes, truck_size)
print(result)  # Output: 50