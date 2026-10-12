def calculate_maximum_height(values):
    current_height = 20
    max_height = 20
    
    for value in values:
        if value == 150:
            current_height -= 5
        else:
            current_height += 1
        
        max_height = max(max_height, current_height)
    
    return max_height

# Test the function with sample input
values = [20, -1, -1, -1, -1, -1, 150]
result = calculate_maximum_height(values)
print(result)