def calculate_absolute_differences(nums):
    n = len(nums)
    partial_sum = [0] * n
    total = sum(nums)

    for i in range(1, n):
        partial_sum[i] = partial_sum[i - 1] + nums[i - 1]

    result = []
    for i in range(n):
        current_sum = sum(nums[:i]) + sum(nums[i+1:])
        result.append(total - 2 * partial_sum[i] - n * nums[i] + 2 * i * nums[i] - total)

    return result

# Test the function
nums = [2, 3, 5]
print(calculate_absolute_differences(nums))