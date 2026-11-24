# Sort the input array
nums.sort()

# Initialize pointers
i, j = 0, len(nums) - 1

# Initialize variable to store the minimized maximum pair sum
max_pair = 0

# Pair elements and update max_pair
while i < j:
    max_pair = max(max_pair, nums[i] + nums[j])
    i += 1
    j -= 1

# Return the minimized maximum pair sum
return max_pair