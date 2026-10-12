# Initialize left and right pointers
left = 0

# Iterate through the array
for right in range(len(nums)):
    # Check if the right pointer is pointing to a non-zero number
    if nums[right] != 0:
        # Swap the non-zero number with the leftmost zero
        nums[right], nums[left] = nums[left], nums[right]
        # Increment the left pointer after the swap
        left += 1