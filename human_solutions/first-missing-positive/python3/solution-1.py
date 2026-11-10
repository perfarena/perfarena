# Remove non-positive integers
nums = [n for n in numbers if n > 0]

# Convert values to index numbers
for n in nums:
    index = abs(n) - 1
    if index < len(nums) and nums[index] > 0:
        nums[index] *= -1

# Find the smallest missing positive integer
for i in range(len(nums)):
    if nums[i] > 0:
        return i + 1

# If all positive integers are present, return the next integer
return len(nums) + 1