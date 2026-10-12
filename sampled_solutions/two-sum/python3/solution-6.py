# Iterate through the list
for idx, value in enumerate(nums):
    # Calculate the required number to reach the target
    required_number = target - value
    
    # Check if the required number is in the dictionary
    if required_number in seen_values:
        # If found, return the indices
        return [seen_values[required_number], idx]
    
    # Update the dictionary with the current value and index
    seen_values[value] = idx