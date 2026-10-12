# Loop through all elements in nums
for i in range(len(nums)):
    # Create pairs with other elements in nums
    for j in range(i + 1, len(nums)):
        # Check if the pair sums up to Target
        if nums[i] + nums[j] == Target:
            return [i, j]  # Return the indices of the two numbers