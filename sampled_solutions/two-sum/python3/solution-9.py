# Initialize a dictionary to store numbers and their indices
seen = {}

# Loop through all elements in nums
for i in range(len(nums)):
    num = nums[i]
    complement = Target - num
    
    # Check if the complement is in the dictionary
    if complement in seen:
        return [seen[complement], i]  # Return the indices if found
    
    # Add the current number and its index to the dictionary
    seen[num] = i