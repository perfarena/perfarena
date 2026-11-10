# Create a hashmap to store numbers and their indices
pair_index = {}

# Iterate through all numbers in the input array
for index, num in enumerate(input_array):
    difference = target - num
    
    # Check if the difference exists in the hashmap
    if difference in pair_index:
        return [index, pair_index[difference]]
    else:
        # Store the current number and its index in the hashmap
        pair_index[num] = index

# If no pair is found, return None or handle accordingly