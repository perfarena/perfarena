def maxProduct(nums):
    current_max = 1
    result = nums[0]

    for num in nums:
        current_max, current_min = min(num, current_max * num, current_min * num), max(num, current_max * num, current_min * num)
        result = max(result, current_max)

    return result

# Test the code
nums = [2, 3, -2, 4]
print(maxProduct(nums))