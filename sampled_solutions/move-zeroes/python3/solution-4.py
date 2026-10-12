# Initialize index variable
index = 0

# Place non-zero elements at the beginning
for i in range(len(nums)):
    if nums[i] != 0:
        nums[index] = nums[i]
        index += 1

# Fill remaining positions with zeros
for i in range(index, len(nums)):
    nums[i] = 0