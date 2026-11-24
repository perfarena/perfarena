def minimize_max_pair_sum(nums):
    nums.sort()
    n = len(nums)
    max_pair_sum = 0
    
    for i in range(n // 2):
        small = nums[i]
        large = nums[n - 1 - i]
        pair_sum = small + large
        max_pair_sum = max(max_pair_sum, pair_sum)
    
    return max_pair_sum

# Test the function
nums = [1, 5, 2, 3, 4, 4]
result = minimize_max_pair_sum(nums)
print(result)  # Output should be 7