def minimize_max_pair_sum(nums):
    nums.sort()
    n = len(nums)
    answer = 0
    
    for i in range(n // 2):
        pair_sum = nums[i] + nums[n - 1 - i]
        answer = max(answer, pair_sum)
    
    return answer

# Example usage
nums = [3, 9, 5, 6, 2, 5]
result = minimize_max_pair_sum(nums)
print(result)  # Output should be 11