def maxProduct(nums):
    if not nums:
        return 0

    overall_max = current_max = current_min = nums[0]
    for num in nums[1:]:
        if num < 0:
            current_max, current_min = current_min, current_max

        current_max = max(num, current_max * num)
        current_min = min(num, current_min * num)

        overall_max = max(overall_max, current_max)

    return overall_max

# Test the function with an example array
nums = [2, 6, -2, 4]
result = maxProduct(nums)
print(result)