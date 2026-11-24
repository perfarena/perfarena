def pivot_index(nums):
    n = len(nums)
    left = 0

    for i in range(n):
        right = sum(nums) - nums[i] - left
        if left == right:
            return i
        left += nums[i]

    return -1

# Test the function
nums = [1, 7, 3, 6, 5, 6]
print(pivot_index(nums))  # Output should be 3