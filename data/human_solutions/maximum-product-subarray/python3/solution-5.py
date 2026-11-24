# Initialize variables
prev_min = prev_max = global_max = float('-inf')

# Iterate through the array
for num in nums:
    # Calculate current min and max
    curr_min = min(num, num * prev_min, num * prev_max)
    curr_max = max(num, num * prev_min, num * prev_max)
    
    # Update global max if needed
    global_max = max(global_max, curr_max)
    
    # Update previous min and max for next iteration
    prev_min, prev_max = curr_min, curr_max

# Return the global maximum product
return global_max