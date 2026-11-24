def minimize_max_pair_sum(nums):
    nums.sort()
    left, right = 0, len(nums) - 1
    result = float('-inf')
    
    while left < right:
        pair_sum = nums[left] + nums[right]
        result = max(result, pair_sum)
        left += 1
        right -= 1
    
    return result

# Test the function
nums = [2, 3, 4, 5, 6]
print(minimize_max_pair_sum(nums))  # Output: 8