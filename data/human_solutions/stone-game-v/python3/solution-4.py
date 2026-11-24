def sum_absolute_differences(nums):
    n = len(nums)
    total_sum = sum(nums)
    preum = 0
    result = [0] * n

    for i in range(n):
        total_sum -= nums[i]
        left_sum = nums[i] * i - preum
        right_sum = (total_sum - nums[i] * (n - i - 1))
        result[i] = left_sum + right_sum
        preum += nums[i]

    return result

# Example usage
nums = [2, 3, 5]
result = sum_absolute_differences(nums)
print(result)