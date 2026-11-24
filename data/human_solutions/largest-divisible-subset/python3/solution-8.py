def largestDivisibleSubset(nums):
    nums.sort()
    
    n = len(nums)
    dp = [1] * n
    prev = [-1] * n
    max_len = 1
    max_idx = 0
    
    for i in range(n):
        for j in range(i):
            if nums[i] % nums[j] == 0 and dp[j] + 1 > dp[i]:
                dp[i] = dp[j] + 1
                prev[i] = j
                if dp[i] > max_len:
                    max_len = dp[i]
                    max_idx = i
    
    result = []
    while max_idx != -1:
        result.append(nums[max_idx])
        max_idx = prev[max_idx]
    
    return result[::-1]

# Input array
nums = [1, 2, 3, 4, 6, 8]

# Finding the largest divisible subset
result = largestDivisibleSubset(nums)
print(result)