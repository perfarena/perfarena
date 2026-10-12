def get_sum_absolute_difference(nums):
    length = len(nums)
    left_sum = [0] * length
    right_sum = [0] * length

    # Calculate left_sum
    for i in range(1, length):
        left_sum[i] = left_sum[i - 1] + (nums[i] - nums[i - 1]) * i

    # Calculate right_sum
    for i in range(length - 2, -1, -1):
        right_sum[i] = right_sum[i + 1] + (nums[i + 1] - nums[i]) * (length - 1 - i)

    # Combine left_sum and right_sum to get the result
    result = [left_sum[i] + right_sum[i] for i in range(length)]

    return result

# Test the function
nums = [1, 4, 6, 8]
result = get_sum_absolute_difference(nums)
print(result)