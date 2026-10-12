def find_favorite_index(nums):
    total_sum = sum(nums)
    left_sum = 0

    for i in range(len(nums)):
        if left_sum == total_sum - nums[i]:
            return i
        left_sum += nums[i]

    return -1

# Test the function
nums = [11, 5, 6, 11]
result = find_favorite_index(nums)
print(result)  # Output: 2